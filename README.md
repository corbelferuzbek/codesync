# CodeSync

## Render sozlamalari
Start Command:
    gunicorn app:app --workers 1 --threads 8 --timeout 120

(workers=1 shart: ishlayotgan jarayonlar va terminal chiqishi xotirada saqlanadi.)

Environment o'zgaruvchilari:
- SECRET_KEY   — uzun tasodifiy qator (majburiy emas, lekin tavsiya etiladi)
- ADMIN_USER   — admin login (standart: 33maktabit)
- ADMIN_PASS   — admin paroli. O'rnatilmasa admin panel o'chiq turadi. Parolni kodga yozmang!
- INVITE_CODE  — (ixtiyoriy) o'rnatilsa, ro'yxatdan o'tish uchun taklif kodi so'raladi
- ICE_SERVERS  — (ixtiyoriy) TURN server, JSON: [{"urls":"turn:host:3478","username":"u","credential":"p"}]
- DB_PATH      — (ixtiyoriy) bazaga yo'l, Render Disk ulangan bo'lsa shu yerga ko'rsating

Eslatma: Render'ning bepul diskida baza, uploads/ va sandbox/ qayta deploy qilinganda o'chadi.

## Admin panel
/admin — login: ADMIN_USER, parol: ADMIN_PASS
