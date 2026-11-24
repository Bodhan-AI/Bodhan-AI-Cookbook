import requests
import json

BASE_URL = 'http://127.0.0.1:8000/api'

print('=== STARTING COMPLETE WORKFLOW TESTS ===')

# WORKFLOW 1: Farmer Registration & Login
print('\n--- WORKFLOW 1: Farmer Registration & Login ---')
reg_payload = {
    'full_name': 'Suresh Kumar',
    'email': 'suresh.test@pestsignal.org',
    'phone': '+91 98481 99999',
    'password': 'password123',
    'role': 'farmer',
    'state': 'Telangana',
    'district': 'Warangal',
    'preferred_language': 'te'
}
r = requests.post(f'{BASE_URL}/auth/register', json=reg_payload)
# 201 Created or 400 if already exists
assert r.status_code in [201, 400], f'Registration failed: {r.status_code}'

r = requests.post(f'{BASE_URL}/auth/login', json={'email': 'suresh.test@pestsignal.org', 'password': 'password123'})
assert r.status_code == 200, f'Login failed: {r.text}'
farmer_token = r.json()['access_token']
print('Farmer registration and login verified! JWT token acquired.')

# WORKFLOW 2: Voice Reporting / Audio Transcribe with Bodhan Indic-Transcribe
print('\n--- WORKFLOW 2: Audio Transcribe with Bodhan Indic-Transcribe ---')
audio_sample = r'C:\Users\ACER\OneDrive\Desktop\IIITD\Bodhan-AI-Cookbook\sample_data\sample_hindi.wav'
with open(audio_sample, 'rb') as f:
    files = {'file': ('sample_hindi.wav', f, 'audio/wav')}
    data = {'language': 'hi'}
    headers = {'Authorization': f'Bearer {farmer_token}'}
    r = requests.post(f'{BASE_URL}/ai/transcribe', headers=headers, files=files, data=data)
assert r.status_code == 200, f'Transcribe endpoint failed: {r.text}'
transcribe_data = r.json()
print('Transcribe Status:', transcribe_data.get('success'))
print('Transcribed text snippet (escaped):', transcribe_data.get('text', '')[:40].encode('ascii', 'backslashreplace').decode('ascii'))
audio_path = transcribe_data.get('audio_file_path')

# WORKFLOW 3: Translation with Bodhan Indic-Translate
print('\n--- WORKFLOW 3: Translation with Bodhan Indic-Translate ---')
trans_payload = {
    'text': 'నా పత్తి పంటలో ఆకులపై బ్రౌన్ మచ్చలు వస్తున్నాయి మరియు పూత రాలిపోతుంది.',
    'source_language': 'te',
    'target_language': 'en'
}
r = requests.post(f'{BASE_URL}/ai/translate', headers=headers, json=trans_payload)
assert r.status_code == 200, f'Translate failed: {r.text}'
translate_data = r.json()
print('Translated observation:', translate_data.get('translated_text'))
print('Engine used:', translate_data.get('engine'))

# WORKFLOW 4: Report Submission
print('\n--- WORKFLOW 4: Report Submission ---')
report_payload = {
    'crop_name': 'Cotton',
    'crop_variety': 'Hybrid RCH',
    'growth_stage': 'Flowering stage',
    'symptoms': 'Brown spots, Yellow leaves, Leaf curling',
    'custom_symptom': 'Observed severe shedding of flower buds',
    'source_language': 'te',
    'original_transcription': 'నా పత్తి పంటలో ఆకులపై బ్రౌన్ మచ్చలు వస్తున్నాయి మరియు పూత రాలిపోతుంది.',
    'english_translation': translate_data.get('translated_text'),
    'audio_file_path': audio_path,
    'submission_method': 'voice',
    'state': 'Telangana',
    'district': 'Warangal',
    'village': 'Parkal',
    'farm_location': 'Sector 4',
    'latitude': 18.201,
    'longitude': 79.712,
    'observation_date': '2026-11-21',
    'affected_plants': 180
}
r = requests.post(f'{BASE_URL}/reports', headers=headers, json=report_payload)
assert r.status_code == 201, f'Create report failed: {r.text}'
new_report = r.json()
new_report_id = new_report['id']
print('Created Report ID:', new_report_id)
print('Report Status:', new_report['investigation_status'])

# WORKFLOW 5: Report Management (Farmer view)
print('\n--- WORKFLOW 5: Report Management (Farmer view) ---')
r = requests.get(f'{BASE_URL}/reports', headers=headers)
assert r.status_code == 200
farmer_reports = r.json()
report_ids = [rep['id'] for rep in farmer_reports]
assert new_report_id in report_ids, 'Submitted report not found in farmer list'
print(f'Farmer sees {len(farmer_reports)} reports, including newly submitted {new_report_id}.')

# WORKFLOW 6: Officer Investigation
print('\n--- WORKFLOW 6: Officer Investigation ---')
r = requests.post(f'{BASE_URL}/auth/login', json={'email': 'officer@pestsignal.org', 'password': 'officer123'})
officer_token = r.json()['access_token']
officer_headers = {'Authorization': f'Bearer {officer_token}'}

# Officer updates status to 'Investigation' and adds internal note
update_payload = {
    'status': 'Investigation',
    'internal_note': 'Dispatched field assistant to Parkal village for leaf spot diagnosis.'
}
r = requests.patch(f'{BASE_URL}/reports/{new_report_id}/status', headers=officer_headers, json=update_payload)
assert r.status_code == 200
updated_rep = r.json()
assert updated_rep['investigation_status'] == 'Investigation'
print(f'Report {new_report_id} status successfully updated to Investigation!')

# WORKFLOW 7: Outbreak Detection
print('\n--- WORKFLOW 7: Outbreak Detection Engine ---')
r = requests.post(f'{BASE_URL}/alerts/detect', headers=officer_headers)
assert r.status_code == 200
alerts = r.json()
print(f'Outbreak detection completed. Active alerts found: {len(alerts)}')
for a in alerts:
    print(f"Alert ID: {a['id']} | Crop: {a['crop_name']} | Region: {a['district']}, {a['state']} | Reports: {a['matching_report_count']} | Severity: {a['severity']}")
    print(f"Disclaimer: {a.get('disclaimer')}")

# WORKFLOW 8: Analytics
print('\n--- WORKFLOW 8: Analytics Overview ---')
r = requests.get(f'{BASE_URL}/analytics/overview', headers=officer_headers)
assert r.status_code == 200
overview = r.json()
print('Database Analytics Overview:', json.dumps(overview, indent=2))
assert overview['total_reports'] >= 10, 'Expected at least 10 reports in database'

# WORKFLOW 9: Security Verification
print('\n--- WORKFLOW 9: Security Verification ---')
# Farmer attempts to access another farmer's report details:
# PS-2026-1008 belongs to farmer2 (Rajesh Patel)
r = requests.get(f'{BASE_URL}/reports/PS-2026-1008', headers=headers)
print('Farmer access to other user report status code:', r.status_code)
assert r.status_code == 403, f'Expected 403 Forbidden, got {r.status_code}'
print('Security verified: Unauthorized report access strictly forbidden (403)!')

# Farmer attempts to trigger outbreak detection (Admin/Officer only):
r = requests.post(f'{BASE_URL}/alerts/detect', headers=headers)
print('Farmer access to admin outbreak detection status code:', r.status_code)
assert r.status_code == 403, f'Expected 403 Forbidden, got {r.status_code}'
print('Security verified: Role-based permissions strictly enforced (403)!')

print('\n=== ALL WORKFLOW TESTS PASSED 100% SUCCESSFULLY! ===')
