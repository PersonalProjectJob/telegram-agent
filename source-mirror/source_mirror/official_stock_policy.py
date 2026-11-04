"""Single stock candidate identity; this is not a grant of media rights."""
PEXELS_URL='https://www.pexels.com/photo/cosmetics-for-manicure-4677843/'
PEXELS_LICENSE='https://www.pexels.com/license/'
PEXELS_IMAGE_SHA='7dd7145d4f4f5104b918eae4a8b55d96130e6501fa1184d5135f11bd751a6f36'
PEXELS_CREDIT='Leeloo The First / Pexels'
PEXELS_ALT='Ảnh stock minh họa chung về dụng cụ làm móng'

def is_candidate(image):
    if image.get('provenance_url')!=PEXELS_URL: return False
    if (image.get('sha256')!=PEXELS_IMAGE_SHA or image.get('credit')!=PEXELS_CREDIT
            or image.get('alt')!=PEXELS_ALT): raise ValueError('stock media identity or attribution mismatch')
    return True

def require_license(image,record):
    if not is_candidate(image) or not isinstance(record,dict) or record.get('rights_evidence_url')!=PEXELS_LICENSE:
        raise ValueError('invalid stock rights evidence URL')
