from django.conf import settings


def public_site_metadata(request) -> dict[str, str]:
    return {"icp_license_number": settings.ICP_LICENSE_NUMBER}
