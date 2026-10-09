# استقرار برنامه روی PythonAnywhere

این نسخه با Flask و WSGI آماده شده تا از روش معمول PythonAnywhere استفاده کند. خود PythonAnywhere برنامه را اجرا می‌کند؛ نیازی نیست کامپیوتر شما روشن بماند یا دستور uvicorn را اجرا کنید.

## پیش‌نیاز مهم دربارهٔ حساب

برای تماس سرور با `api.divar.ir` دسترسی اینترنت خروجی لازم است. طبق راهنمای PythonAnywhere، حساب‌های رایگان فقط به مقصدهای فهرست مجاز دسترسی دارند و حساب‌های پولی دسترسی اینترنت خروجی نامحدود دارند. چون این برنامه از endpoint وبِ غیررسمی دیوار استفاده می‌کند، روی حساب رایگان احتمالاً درخواست به دیوار مسدود می‌شود. [محدودیت شبکهٔ حساب رایگان](https://help.pythonanywhere.com/pages/403ForbiddenError/)

برای نمایش فهرست دسته‌بندی‌ها نیز endpoint رسمی کنار دیوار کلید API می‌خواهد. در فایل WSGI و پیش از import کردن `flask_app`، این خط را با کلید خودت اضافه کن:

```python
import os
os.environ["DIVAR_KENAR_API_KEY"] = "YOUR_KENAR_API_KEY"
```

## ۱) بارگذاری فایل‌ها

از بخش **Files** در PythonAnywhere پوشه‌ای مثل `divar_app` بساز و این فایل‌ها را داخلش بارگذاری کن:

- `flask_app.py`
- `divar_api.py`
- `index.html`
- `requirements.txt`

## ۲) ساخت محیط و نصب وابستگی‌ها

از تب **Consoles** یک Bash console باز کن و نسخهٔ پایتونی را که برای وب‌اپ انتخاب می‌کنی به‌کار ببر. نمونه (نسخه را با نسخهٔ انتخاب‌شده در پنل هماهنگ کن):

```bash
mkvirtualenv --python=/usr/bin/python3.13 divar-env
workon divar-env
pip install -r /home/YOUR_USERNAME/divar_app/requirements.txt
```

`YOUR_USERNAME` را با نام کاربری PythonAnywhere خودت عوض کن.

## ۳) ساخت Web app

در تب **Web** گزینهٔ **Add a new web app** را بزن، دامنهٔ رایگان خودت را انتخاب کن، سپس **Manual configuration** و همان نسخهٔ Python محیط مجازی را انتخاب کن. در قسمت **Virtualenv** مسیر زیر را وارد کن:

```text
/home/YOUR_USERNAME/.virtualenvs/divar-env
```

## ۴) تنظیم WSGI

در تب **Web** روی فایل پیکربندی WSGI کلیک کن و محتوایش را با این کد جایگزین کن. نام کاربری را عوض کن:

```python
import sys

project_home = "/home/YOUR_USERNAME/divar_app"
if project_home not in sys.path:
    sys.path.insert(0, project_home)

from flask_app import app as application
```

ذخیره کن و در تب **Web** دکمهٔ **Reload** را بزن. صفحهٔ برنامه روی دامنهٔ `YOUR_USERNAME.pythonanywhere.com` باز می‌شود.

## عیب‌یابی

اگر صفحه خطا داد، فایل‌های **Error log** و **Server log** را از تب Web باز کن. اگر خطای اتصال به دیوار دیدی، دسترسی اینترنت خروجی حساب را بررسی کن. برای راهنمای رسمی Flask در PythonAnywhere به [مستندات آن‌ها](https://help.pythonanywhere.com/pages/Flask/) مراجعه کن.
