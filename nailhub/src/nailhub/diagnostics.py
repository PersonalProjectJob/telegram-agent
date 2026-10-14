"""Bounded, structured field diagnostics; no raw exception or model response text."""
import json
import re

LABELS = {'salon': 'Tên tiệm', 'address': 'Địa chỉ salon', 'city': 'Thành phố',
          'state': 'Tiểu bang', 'zip': 'Mã bưu điện', 'email': 'Email',
          'phone_primary': 'Số điện thoại chính', 'phone_other': 'Số điện thoại khác',
          'salary_amount': 'Mức lương', 'salary_type': 'Loại lương',
          'startDate': 'Ngày bắt đầu', 'endDate': 'Ngày kết thúc', 'category': 'Danh mục',
          'isPublic': 'Trạng thái công khai', 'automation': 'Tự động hóa'}


def safe(value, limit=220):
    text = ' '.join(str(value or '').split())
    if re.search(r'password|passwd|secret|token|api[_ -]?key|authorization|bearer|mật khẩu', text, re.I):
        return '[Đã ẩn dữ liệu nhạy cảm]'
    return ''.join(c for c in text if c.isprintable())[:limit]


def clean(details):
    if not isinstance(details, dict):
        return {}
    field = details.get('field')
    if field not in LABELS:
        return {}
    return {'kind': 'data' if details.get('kind') == 'data' else 'automation', 'field': field,
            **{key: safe(details.get(key, '')) for key in ('current', 'source', 'evidence', 'action')}}


def failure_details(error, post, intended=None):
    details = clean(getattr(error, 'details', None))
    if not details:
        reason = str(error)
        path = getattr(error, 'path', '')
        field = path.split('.')[1] if path.startswith('location.') else ('salary_amount' if path.startswith(('salary.', 'salary_candidates')) or reason.startswith(('salary ', 'ambiguous salary', 'unrecognized amount', 'single potential', 'another monetary')) else 'automation')
        details = clean({'field': field, 'kind': 'automation',
            'source': 'Chưa xác định: dữ liệu trích xuất chưa được xác nhận',
            'action': 'Đối chiếu trường với nội dung gốc; thử lại trích xuất hoặc sửa thủ công, không xem lỗi model là dữ liệu tin sai.'})
        if field == 'automation':
            details['action'] = 'Kiểm tra phiên đăng nhập, giao diện, kết nối và trạng thái lưu/duyệt hiện tại; xử lý nguyên nhân rồi thử lại.'
    field = details['field']
    if not details['current'] and field in post.fields:
        details['current'] = safe(post.fields[field])
    corrections = (intended or {}).get('field_corrections', {})
    if field == 'automation' and corrections:
        key = 'state' if 'state' in corrections else next(iter(corrections))
        correction = corrections[key]
        details = clean({'kind': 'automation', 'field': key, **correction,
            'action': 'Tự động hóa chưa hoàn tất. Đối chiếu giá trị nguồn với form và trạng thái lưu/duyệt trước khi sửa hoặc thử lại.'})
    return details


def render(raw):
    try:
        value = json.loads(raw) if isinstance(raw, str) else raw
    except (ValueError, TypeError):
        value = {}
    detail = clean(value)
    if not detail:
        return '\nTrường: Tự động hóa\nHiện tại / nguồn: Chưa xác định.\nCần làm: Mở tin, đối chiếu thông tin gốc và trạng thái lưu/duyệt; kiểm tra nhật ký kỹ thuật.'
    label = 'Dữ liệu cần xác nhận' if detail['kind'] == 'data' else 'Lỗi tự động hóa / trích xuất'
    return (f'\nPhân loại: {label}\nTrường: {LABELS[detail["field"]]}'
            f'\nGiá trị đã đọc: {detail["current"] or "(Trống / chưa xác định)"}'
            f'\nNguồn: {detail["source"] or "Chưa có giá trị nguồn được xác nhận"}'
            + (f'\nTrích dẫn: {detail["evidence"]}' if detail['evidence'] else '')
            + f'\nCần làm: {detail["action"] or "Đối chiếu nguồn và sửa trường này trước khi duyệt."}')
