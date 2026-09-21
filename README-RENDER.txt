نسخة جاهزة للنشر على Render.

1) في GitHub استبدل app.py القديم بهذا app.py.
2) تأكد أن requirements.txt موجود في جذر المشروع.
3) في Render اجعل Start Command:
gunicorn app:application --bind 0.0.0.0:$PORT --workers 1 --timeout 120
4) اعمل Manual Deploy ثم Deploy latest commit.
