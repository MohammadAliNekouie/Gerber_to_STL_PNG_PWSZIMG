# Gerber to layer, STL and PNG

این ابزار آفلاین، زیرمجموعه‌ای عملی از RS-274X Gerber را می‌خواند و هندسه را به `pwszImg`، STL باینری و PNG تبدیل می‌کند. نسخه‌ی اصلی این پروژه `gerber_to_layer_v2.py` است.

## نصب

Python 3.8 یا جدیدتر، Pillow و Shapely لازم است:

```bash
python -m pip install Pillow Shapely
```

Shapely برای `--invert` و تولید STL لازم است. همه‌ی پردازش‌ها و فایل‌ها محلی هستند؛ این محیط Windows را آزمایش نکرده است.

## اجرای معمولی

```bash
python gerber_to_layer_v2.py Soldermask_Top.gbr --verify
```

خروجی‌ها همیشه در پوشه‌ی `output` در همان پوشه‌ای که فایل `gerber_to_layer_v2.py` قرار دارد ساخته می‌شوند؛ این مسیر به محل فایل Gerber یا پوشه‌ی کاری وابسته نیست. پوشه در صورت نیاز خودکار ساخته می‌شود و نام فایل‌ها از stem ورودی می‌آید: `Soldermask_Top.pwszImg`، `Soldermask_Top.stl` و `Soldermask_Top.png`. در اجرای تعاملی که `__file__` در دسترس نباشد، مسیر `output` نسبت به پوشه‌ی کاری انتخاب می‌شود.

## وارونه‌سازی هندسی

با `--invert`، ابتدا `Keep-out.gbr` به‌عنوان قاب و Gerber ورودی به‌عنوان ناحیه‌ی حذف‌شونده parse می‌شوند. اگر قاب به‌صورت `G36/G37` پرشده نباشد و فقط از strokeهای `D01` یا خط/قوس بسته تشکیل شده باشد، نقاط آن به یک polygon بسته تبدیل می‌شوند (convex hull؛ برای قاب‌های مستطیلی معادل ماسک پرشده‌ی کل برد است). سپس دقیقاً این عملیات انجام می‌شود:

```text
output geometry = frame.difference(input geometry)
```

همین هندسه‌ی نهایی برای هر سه خروجی `pwszImg`، STL و PNG استفاده می‌شود. خروجی شامل سطح پرشده‌ی قاب منهای پدهاست؛ STL با triangulation همین Polygon/holes و دیواره‌های بسته ساخته می‌شود و PNG در 300 DPI با ابعاد 3200×2240 ذخیره می‌گردد. بنابراین قاب خطی دیگر به خروجی نازک یا تصویر سفید/خالی تبدیل نمی‌شود. قاب پیش‌فرض `Keep-out.gbr` در همان پوشه‌ی فایل ورودی است. با `--frame` می‌توان قاب دیگری داد:

```bash
python gerber_to_layer_v2.py Soldermask_Top.gbr --invert --verify
python gerber_to_layer_v2.py Soldermask_Bot.gbr --invert --frame Keep-out.gbr --verify
```

اگر قاب پیش‌فرض وجود نداشته باشد، برنامه با خطای روشن متوقف می‌شود. استفاده از خود فایل ورودی به‌عنوان قاب در حالت وارونه‌سازی مجاز نیست و با خطا رد می‌شود. اگر ورودی کل قاب را بپوشاند، نتیجه‌ی خالی خطا محسوب می‌شود. برای اعتبارسنجی، تعداد facet چاپ‌شده‌ی STL باید بزرگ‌تر از صفر باشد و `--verify` باید تعداد رکوردهای `pwszImg` و مختصات float32 را تأیید کند.

## نمونه‌ی تست وارونه‌سازی

در پوشه‌ی شامل سه فایل Gerber:

```bash
python gerber_to_layer_v2.py Soldermask_Top.gbr --invert --frame Keep-out.gbr --verify
python gerber_to_layer_v2.py Soldermask_Bot.gbr --invert --frame Keep-out.gbr --verify
```

در این حالت قاب `Keep-out.gbr` به سطح بسته تبدیل و پدهای لایه از آن کم می‌شوند. خروجی‌های سه‌گانه در `output` کنار همین script نوشته می‌شوند و گزارش اجرای برنامه تعداد facetهای STL و تعداد segmentهای `pwszImg` را نشان می‌دهد.

## گزینه‌های CLI

| گزینه | شرح |
|---|---|
| `GERBER` | مسیر فایل Gerber ورودی؛ اجباری است. |
| `--invert` | محاسبه‌ی قاب منهای هندسه‌ی ورودی قبل از تولید همه‌ی خروجی‌ها. |
| `--frame PATH` | قاب برای `--invert`؛ پیش‌فرض `Keep-out.gbr` کنار ورودی. |
| `--reference PATH` | استفاده از header/trailer فایل `pwszImg` مرجع. |
| `--thickness N` | ضخامت STL بر حسب mm؛ پیش‌فرض `0.1`. |
| `--line-width N` | عرض پیش‌فرض stroke؛ پیش‌فرض `0.1`. |
| `--verify` | بررسی تعداد رکورد و مختصات float32 در `pwszImg`. |

برای فهرست کامل help:

```bash
python gerber_to_layer_v2.py --help
```

## محدودیت‌ها

این parser مفسر کامل همه‌ی Aperture Macroها، قطبیت‌های پیچیده و همه‌ی دستورات Gerber نیست. STL بر پایه‌ی union هندسه‌ی پرشده و triangulation Shapely ساخته می‌شود؛ قبل از استفاده‌ی تولیدی، خروجی را در viewer و ابزار بررسی مش کنترل کنید. خروجی PNG برای preview است و نباید به‌عنوان مرجع ابعادی مستقل استفاده شود.
