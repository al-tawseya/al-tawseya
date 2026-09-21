# Al-Tawseya — Render deployment

ضع الملفات الثلاثة في جذر مستودع GitHub:
- app.py
- requirements.txt
- render.yaml

إعدادات Render:
Build Command:
pip install -r requirements.txt

Start Command:
gunicorn app:application --bind 0.0.0.0:$PORT --workers 1 --timeout 120

Health Check:
 /healthz

ملاحظة:
Gemini اختياري في هذا المشروع. التطبيق يعمل بالمحلل العربي المحلي حتى بدون GOOGLE_API_KEY.
