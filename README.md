# التوصية v2 — محرك التوصية الأردني

نسخة الإنتاج الجاهزة للنشر. تعمل بدون أي مفتاح خارجي (المستخرج المحلي حتمي)،
ومفتاح Gemini اختياري لتحسين فهم اللهجة.

## التشغيل محليًا

```bash
pip install -r requirements.txt
python app.py            # http://127.0.0.1:8000
```

## الاختبارات

```bash
pytest tests/ -v
```

## النشر على Render

1. ارفعي الملفات: `app.py`, `requirements.txt`, `gunicorn.conf.py`, `render.yaml`
2. Render يقرأ `render.yaml` تلقائيًا (health check على `/healthz`)
3. أضيفي `GOOGLE_API_KEY` من لوحة Render → Environment (المفتاح غير موجود في الكود)

## Docker

```bash
docker build -t tawseya .
docker run -p 8000:8000 -e GOOGLE_API_KEY="..." tawseya
```

## نقاط النهاية

| المسار | الوصف |
|---|---|
| `GET /` | الواجهة |
| `GET /healthz` | فحص صحة Render/Docker |
| `GET /api/health` | معلومات الخدمة |
| `POST /api/recommend` | `{query}` → نية + نتائج + session_id |
| `POST /api/refine` | `{session_id, message}` → تحديث النية («بدي أرخص») |

## ⚠️ أمان

- المفتاح القديم المكشوف في `.env.txt` و`render.yaml` الأصليين يجب إلغاؤه (revoke) فورًا من Google AI Studio.
- لا تُلتزم `.env` ولا `*.db` في Git (موجودان في `.gitignore`).
