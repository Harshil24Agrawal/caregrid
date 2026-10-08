"""Small shared vocabularies (DATA_SPEC §1). Kept out of models.py, which mirrors CONTRACTS exactly."""
from __future__ import annotations

REQUEST_TYPES = [
    "general_policy_question",
    "provider_address_change",
    "provider_name_change",
    "portal_access_reset",
    "prior_auth_status",
    "dme_equipment_request",
    "claim_status_inquiry",
    "complaint_grievance",
]

# Precedent/case "facts" key `category` (decision P0-a). Compile and retrieve must share this map.
REQUEST_CATEGORY = {
    "general_policy_question": "policy_info",
    "provider_address_change": "record_update",
    "provider_name_change": "record_update",
    "portal_access_reset": "access",
    "prior_auth_status": "account_status",
    "claim_status_inquiry": "account_status",
    "dme_equipment_request": "equipment",
    "complaint_grievance": "complaint",
    "unknown": "unknown",
}

# Org mailboxes are not personal data; leak scan and anonymizer-driven checks allow this domain.
ORG_EMAIL_DOMAIN = "caregrid.example"


def facts_missing(missing: list[str]) -> str:
    """Canonical value of the `missing` fact: sorted comma-joined field names, or 'none'."""
    return ",".join(sorted(missing)) if missing else "none"
