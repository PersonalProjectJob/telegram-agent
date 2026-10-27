"""R6a: accept the LAST native answer (0..3 pinned nudges), wrap a bare array, producer re-proves it all."""
import hashlib
import json
import unittest
import uuid
from pathlib import Path

from nailblog.agy_text import AgyTextError
from nailblog.images import ImageError
from nailblog.images.agy_native import native_text, wrap_answer
from nailblog.images.subscription import verify_text_receipt
from tests.test_agy_text import Env, assistant_record, user_record, write_conversation
from tests.test_agy_text_nudges import nudge_record

ITEM = {'type': 'object', 'required': ['slot'], 'properties': {'slot': {'type': 'string'}}}
SCHEMA = {'type': 'object', 'required': ['candidates'],
          'properties': {'candidates': {'type': 'array', 'minItems': 1, 'items': ITEM}}}
TWO = {'type': 'object', 'required': ['a', 'b'], 'properties': {'a': {'type': 'array'}, 'b': {'type': 'array'}}}


def arrays(n):
    return [json.dumps([{'slot': '%02d:00' % (i + 6)} for i in range(i + 1)]) for i in range(n)]


def rows_with(prompt, answers, *, tool_at=None):
    """user, answer, then (nudge, answer) pairs: the observed Agy shape."""
    rows = [(0, 14, 3, 0, user_record(prompt), None, None)]
    for i, answer in enumerate(answers):
        if i:
            rows.append((len(rows), 14, 3, 0, nudge_record(), None, None))
        rows.append((len(rows), 15, 3, 0, assistant_record(answer, tool=(i == tool_at)), None, None))
    return rows


class FinalAnswerTests(Env):
    def run_rows(self, answers, *, response=None, schema=SCHEMA, **kw):
        def behaviour(args, timeout):
            prompt = args[args.index('--print') + 1]
            session = str(uuid.uuid4())
            write_conversation(self.conversations / (session + '.db'), rows_with(prompt, answers, **kw))
            return self.envelope(session, answers[-1] if response is None else response)
        self.behaviour = behaviour
        self.beat()
        return self.ask.ask('write json', schema, purpose='run1:write')

    def parts(self):
        receipt = dict(self.ask.last_receipt)
        job = Path(self.cfg.queue_dir) / receipt['job_id']
        return (receipt, json.loads((job / 'request.json').read_bytes()), (job / 'output.json').read_bytes(),
                (job / 'native-final.bin').read_bytes(), (job / 'native-user.bin').read_bytes(),
                (job / 'native-transcript.json').read_bytes())

    def verify(self, parts):
        receipt, request, output, final, user, proof = parts
        return verify_text_receipt(self.cfg, receipt, request, output, final, user, transcript=proof)

    def held(self, answers, **kw):
        with self.assertRaises(AgyTextError) as raised:
            self.run_rows(answers, **kw)
        self.assertEqual(raised.exception.code, 'held', raised.exception.detail)
        self.assertEqual(len(self.calls), 1)
        return raised.exception.detail

    def test_four_different_arrays_three_nudges_accept_final_and_wrap(self):
        answers = arrays(4)
        value = self.run_rows(answers)
        self.assertEqual(value, {'candidates': json.loads(answers[-1])})
        receipt = self.ask.last_receipt
        self.assertEqual((receipt['status'], receipt['answers_count'], receipt['answers_distinct'],
                          receipt['accepted'], receipt['wrapped_array'], receipt['native_steps']),
                         ('complete', 4, 4, 'final', True, [14, 15, 14, 15, 14, 15, 14, 15]))
        self.assertEqual(receipt['answer_shas'], [hashlib.sha256(a.encode()).hexdigest() for a in answers])
        self.assertEqual(self.verify(self.parts()), value)

    def test_middle_answer_not_json_is_held(self):
        answers = arrays(4)
        answers[1] = 'I will think about it'
        self.assertIn('held', self.held(answers))

    def test_tool_call_is_held(self):
        self.assertIn('tool_call_forbidden', self.held(arrays(2), tool_at=0))

    def test_four_nudges_are_held(self):
        self.assertIn('native_rows', self.held(arrays(5)))

    def test_final_answer_failing_schema_is_rejected(self):
        answers = arrays(2)
        answers[-1] = json.dumps([{'slot': 1}])
        with self.assertRaises(AgyTextError) as raised:
            self.run_rows(answers)
        self.assertEqual(raised.exception.code, 'schema')

    def test_final_answer_that_is_a_string_is_held_object_required(self):
        self.assertIn('object_required', self.held([json.dumps('done')]))

    def test_stdout_response_must_equal_final_native_answer(self):
        answers = arrays(2)
        self.assertIn('stdout_native_mismatch', self.held(answers, response=answers[0]))

    def test_producer_detects_tampered_final_answer(self):
        self.run_rows(arrays(4))
        good = self.parts()
        other = json.dumps([{'slot': '23:00'}])
        forged_output = json.dumps({'candidates': json.loads(other)}, ensure_ascii=False,
                                   separators=(',', ':')).encode()
        variants = []
        shas = list(good[0]['answer_shas'])
        shas[-1] = hashlib.sha256(other.encode()).hexdigest()
        variants.append((dict(good[0], answer_shas=shas), *good[1:]))
        variants.append((dict(good[0], accepted='first'), *good[1:]))
        variants.append((dict(good[0], wrapped_array=False), *good[1:]))
        variants.append((dict(good[0], answers_distinct=1), *good[1:]))
        variants.append((good[0], good[1], forged_output, *good[3:]))
        rows = json.loads(good[5])
        rows[-1][4] = assistant_record(other).hex()
        proof = json.dumps(rows).encode()
        variants.append((dict(good[0], native_transcript_sha=hashlib.sha256(proof).hexdigest()), *good[1:5], proof))
        for index, parts in enumerate(variants):
            with self.subTest(variant=index), self.assertRaises(ImageError):
                self.verify(parts)

    def test_object_answer_with_two_property_schema_is_not_wrapped(self):
        obj = {'a': [1], 'b': [2]}
        self.assertEqual(wrap_answer(obj, TWO), (obj, False))
        self.assertEqual(wrap_answer({'candidates': []}, SCHEMA), ({'candidates': []}, False))
        with self.assertRaises(ImageError) as raised:
            wrap_answer([1], TWO)
        self.assertEqual(str(raised.exception), 'cli_text_held_object_required')
        self.assertEqual(self.run_rows([json.dumps(obj)], schema=TWO), obj)
        self.assertIs(self.ask.last_receipt['wrapped_array'], False)

    def test_native_text_accepts_zero_to_three_nudges_only(self):
        for count in range(4):
            native = native_text(rows_with('P', arrays(count + 1)), 'P')
            self.assertEqual((native['answers_count'], native['final_text']), (count + 1, arrays(count + 1)[-1]))
        with self.assertRaises(ImageError):
            native_text(rows_with('P', arrays(5)), 'P')

    def test_prompt_suffix_changes_job_id_and_held_job_is_never_rerun(self):
        answers = arrays(2)
        answers[0] = 'nope'
        self.held(answers)
        for _ in range(2):
            with self.assertRaises(AgyTextError) as raised:
                self.ask.ask('write json', SCHEMA, purpose='run1:write')
            self.assertEqual(raised.exception.code, 'held')
        self.assertEqual(len(self.calls), 1)
        request = json.loads(next(Path(self.cfg.queue_dir).glob('*/request.json')).read_bytes())
        self.assertTrue(request['prompt'].startswith('write json\n\n'))


if __name__ == '__main__':
    unittest.main()
