"""Bounded plain-text manual review notices. Never expose raw content or exception text."""

import json
import re
from .diagnostics import render, safe

VERSION = 2
UUID = re.compile(r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$', re.I)


def units(text):
    return len(text.encode('utf-16-le', errors='replace')) // 2


def bounded(text, maximum):
    text = ' '.join(''.join(c if c.isprintable() else ' ' for c in str(text)).split())
    result = []
    size = 0
    for character in text:
        size += 2 if ord(character) > 0xffff else 1
        if size > maximum:
            break
        result.append(character)
    return ''.join(result)


def reason_text(reason):
    # Translate fixed validator outcomes only; unknown exception/source text is never echoed.
    exact = {
        'invalid evidence span': 'Dữ liệu trích xuất chưa chỉ rõ nguồn. Mở tin và kiểm tra thông tin gốc trước khi duyệt.',
        'evidence quote must be exact, bounded and unique in original': 'Trích dẫn nguồn chưa khớp duy nhất. Kiểm tra thông tin gốc trước khi duyệt.',
        'location conflicts with current field': 'Địa chỉ hoặc thông tin tiệm trích xuất khác dữ liệu hiện có. Đối chiếu và sửa trường bị lệch.',
        'location not present in original': 'Chưa có nguồn xác nhận thông tin địa chỉ. Kiểm tra địa chỉ và ZIP.',
        'stored location describes a rental house, not the recruiting salon': 'Địa chỉ hiện tại thuộc nhà cho thuê; chưa xác định địa chỉ salon tuyển dụng.',
        'required contact location incomplete or invalid; Owner review required': 'Thiếu hoặc sai tên tiệm, địa chỉ, thành phố hay tiểu bang. Bổ sung từ nguồn xác thực.',
        'state or ZIP syntax invalid; Owner review required': 'Tiểu bang hoặc ZIP chưa hợp lệ. Kiểm tra và sửa trước khi duyệt.',
        'required main phone missing or invalid; Owner review required': 'Số điện thoại chính còn thiếu hoặc sai định dạng. Kiểm tra lại số liên lạc.',
        'phone field contains unrecognized data': 'Thông tin điện thoại chưa đọc được chắc chắn. Kiểm tra số chính và số khác.',
        'paused by Owner': 'Quá trình dừng theo lệnh của bạn. Kiểm tra trạng thái tin trước khi thao tác tiếp.',
        'worker operation failed; review required': 'Thao tác chưa hoàn tất. Kiểm tra trực tiếp trạng thái tin và nhật ký kỹ thuật.',
        'post-save fresh read does not match every expected field': 'Dữ liệu đọc lại sau lưu chưa khớp. Kiểm tra trực tiếp các trường và trạng thái tin.',
        'approval fresh read did not establish expected state': 'Chưa xác nhận được kết quả duyệt. Kiểm tra trạng thái hiện tại trước khi duyệt lại.',
        'mutation outcome cannot be established after restart': 'Sau khởi động lại chưa xác định được kết quả lưu hoặc duyệt. Kiểm tra trạng thái hiện tại.',
        'crash reconciliation read failed': 'Chưa đọc lại được tin sau khởi động lại. Kiểm tra trực tiếp trạng thái hiện tại.',
        'recovered Draft matches proposal; manual continuation required': 'Tin còn Draft sau khôi phục. Mở kiểm tra và tiếp tục thủ công.',
        'save outcome unverified': 'Chưa xác nhận được kết quả lưu. Kiểm tra trực tiếp tin trước khi lưu hoặc duyệt lại.',
        'approval outcome unverified': 'Chưa xác nhận được kết quả duyệt. Kiểm tra trạng thái hiện tại trước khi duyệt lại.',
        'AI completion unverified': 'Chưa xác nhận được nút viết bằng AI đã hoàn tất. Kiểm tra nội dung và trạng thái tin.',
        'edit form differs from original field': 'Dữ liệu trong form sửa khác bản đã đọc. Đối chiếu các trường trước khi lưu.',
        'location widget has not retained original state/city': 'Form chưa giữ đúng thành phố hoặc tiểu bang. Kiểm tra và chọn lại giá trị đúng.',
        'end date conflicts with Owner sentinel start date repair': 'Ngày kết thúc không hợp lệ với ngày bắt đầu được sửa. Kiểm tra và chỉnh ngày thủ công.',
        'selector missing or ambiguous': 'Chưa xác định chắc chắn nút hoặc ô nhập trên trang. Kiểm tra giao diện trước khi thao tác.',
        'required selector readiness could not be established': 'Nút hoặc ô nhập chưa hiển thị sẵn sàng. Kiểm tra trang và kết nối.',
    }
    if reason in exact:
        return exact[reason]
    if reason.startswith('login_required'):
        return 'Phiên admin cần đăng nhập lại. Đăng nhập NailHub trên hồ sơ trình duyệt automation rồi kiểm tra tin.'
    if reason.startswith(('salary ', 'ambiguous pay', 'another monetary', 'other monetary',
                          'another numeric salary', 'selected salary', 'single potential',
                          'cannot assume tips', 'unrecognized amount', 'invalid salary',
                          'explicit work schedule', 'invalid work schedule', 'schedule value',
                          'ambiguous salary', 'ambiguous number', 'reversed salary', 'range rounding')):
        return 'Chưa xác định chắc chắn mức lương hoặc đơn vị thời gian. Đối chiếu nội dung gốc và điền lương tuần, thương lượng hoặc không hiển thị.'
    if reason.startswith(('model ', 'evidence ', 'invalid extraction', 'too many fact')):
        return 'Không trích xuất được dữ liệu có nguồn chắc chắn. Mở tin và kiểm tra thông tin gốc.'
    if reason.startswith(('post changed', 'remote post changed')):
        return 'Tin đã thay đổi trong lúc xử lý. Kiểm tra bản hiện tại trước khi thao tác tiếp.'
    return 'Cần kiểm tra trực tiếp thông tin và trạng thái tin; xem nhật ký kỹ thuật nếu cần.'


def scan_result_text(result):
    if result == 'not scanned' or re.fullmatch(r'scan complete; processed=\d{1,6}', str(result)):
        return str(result)
    if result == 'scan blocked; browser/model unavailable':
        return 'Lượt quét bị dừng do trình duyệt hoặc model chưa sẵn sàng.'
    return reason_text(str(result))


def manual_notice(row):
    if row.get('state') not in ('needs-human', 'uncertain') or not UUID.fullmatch(str(row.get('id', ''))):
        return None
    try:
        title = json.loads(row['original']).get('fields', {}).get('title', '')
    except (ValueError, TypeError, AttributeError, KeyError):
        title = ''
    title = bounded(safe(title, 160), 160) or '(Tin chưa có tiêu đề)'
    label = ('Cần kiểm tra thủ công — chưa xác định kết quả lưu/duyệt' if row['state'] == 'uncertain'
             else 'Tin cần bạn kiểm tra thủ công')
    return (f'NailHub: {label}\n{title}\n'
            f'https://nailhub.ai/admin/postings?id={row["id"]}\n'
            f'Lý do: {reason_text(str(row.get("reason", "")))}'
            + render(row.get('details', '{}')))


def pending_messages(rows, header='NailHub: tin cần kiểm tra thủ công'):
    messages = []
    current = bounded(header, 1000)
    entries = [notice for row in rows if (notice := manual_notice(row))]
    if not entries:
        return [current + '\nKhông có tin đang chờ bạn kiểm tra.']
    for entry in entries:
        if units(current + '\n\n' + entry) > 3500:
            messages.append(current)
            current = entry
        else:
            current += '\n\n' + entry
    messages.append(current)
    return messages
