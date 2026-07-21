"""SoT UNICA degli scope OAuth google-workspace (10/7/2026).

Prima vivevano in TRE liste driftate (google_api.SCOPES, setup.SCOPES, preset
"all" di runtime/skill_oauth_providers.json): il re-consent guidato dal dialog
usava la preset e ha materializzato il drift — token rigenerato SENZA
gmail.send / gmail.readonly / cloud-vision (che il token precedente aveva) →
invio mail e vision rotti in silenzio. Una lista sola + guard di parita'
(`runtime/tests/test_google_scopes_sot.py`): il drift diventa impossibile.

NB: gmail.modify NON implica gmail.send (scope separato). cloud-vision serve a
`vision web_detect`. contacts.readonly basta (People API in sola lettura).
"""

SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.send",
    "https://www.googleapis.com/auth/gmail.modify",
    "https://www.googleapis.com/auth/calendar",
    "https://www.googleapis.com/auth/drive",
    "https://www.googleapis.com/auth/contacts.readonly",
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/documents",
    "https://www.googleapis.com/auth/cloud-vision",
    "https://www.googleapis.com/auth/photoslibrary.appendonly",
    "https://www.googleapis.com/auth/photoslibrary.readonly.appcreateddata",
    "https://www.googleapis.com/auth/photospicker.mediaitems.readonly",
]
