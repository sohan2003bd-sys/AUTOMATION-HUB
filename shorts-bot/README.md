# বাংলা Shorts বট — ধাপ ১ + ২ (আইডিয়া → স্ক্রিপ্ট → ভয়েস → ভিডিও → ইউটিউবে আপলোড)

নিশ: **প্রযুক্তি ও AI টিপস**, ভাষা: **বাংলা**, ১০টি ক্যাটাগরি (`config.yaml`)।
এই ধাপে ভিডিও তৈরি হয় `output/` ফোল্ডারে (`.mp4` + আপলোডের জন্য `.json`)। ইউটিউবে আপলোড ও শিডিউল ধাপ ২-এ।

## সেটআপ
1. Python 3.10+ ও FFmpeg ইনস্টল করুন।
2. `pip install -r requirements.txt`
3. **বাংলা ফন্ট** (খুব জরুরি, নইলে অক্ষর ভেঙে যায়):
   - Ubuntu/GitHub Actions: `sudo apt-get install -y ffmpeg fonts-noto-core libraqm0 libfribidi0`
   - Windows/Mac: Google Fonts থেকে *Noto Sans Bengali* (Bold) নামিয়ে `assets/fonts/` ফোল্ডারে রাখুন (ফোল্ডার বানিয়ে নিন)।
4. AI কী সেট করুন (একটা): `ANTHROPIC_API_KEY` অথবা `GEMINI_API_KEY`, আর `config.yaml`-এ `llm.provider` মিলিয়ে নিন।
   মডেলের নাম `config.yaml`-এ আছে; না চললে আপনার অ্যাকাউন্টে থাকা নামটি বসান।
5. `config.yaml`-এ `channel.handle` নিজের চ্যানেলের নাম দিন।

## চালানো
```
python -m shorts demo --tts edge      # নমুনা ভিডিও, বাংলা ভয়েসসহ (AI কী লাগে না)
python -m shorts ideas --per-category 20
python -m shorts make --count 3       # ৩টি ভিডিও বানাবে (ক্যাটাগরি ঘুরিয়ে ঘুরিয়ে)
python -m shorts status
```
`--tts silent` দিলে ভয়েস ছাড়া দ্রুত পরীক্ষা হয়।

## গুরুত্বপূর্ণ
- প্রথমে `demo`, তারপর ৩–৫টি ভিডিও বানিয়ে **নিজে দেখুন**: তথ্য ঠিক আছে কিনা, উচ্চারণ ঠিক আছে কিনা।
  AI ভুল ধাপ লিখতে পারে; আপলোডের আগে অন্তত শুরুতে নিজে যাচাই করুন।
- ভিডিওগুলো একই ছাঁচের হলে ইউটিউব মনিটাইজেশন কেটে দিতে পারে (inauthentic content নীতি)।
  তাই শুরুতে কম সংখ্যায় (দিনে ৫–১০টি) চালান।
- একটা ভিডিও রেন্ডারে প্রায় আধা মিনিট লাগে (`video.motion: none` দিলে দ্রুত হয়)।

## ফাইল
- `shorts/ideas.py` আইডিয়া, `scriptgen.py` স্ক্রিপ্ট, `tts.py` ভয়েস, `render.py` ভিডিও, `store.py` কাজের তালিকা (`data/queue.jsonl`)।
- `shorts/youtube.py` ইউটিউব আপলোড, `shorts/runner.py` প্রতিদিনের কাজ, `.github/workflows/shorts.yml` প্রতিদিন নিজে চালানো।

## ধাপ ২ — ইউটিউব আপলোড ও প্রতিদিন নিজে চলা
**GitHub Secrets (repo → Settings → Secrets and variables → Actions):**
`YT_CLIENT_ID`, `YT_CLIENT_SECRET`, `YT_REFRESH_TOKEN`, এবং AI কী: `ANTHROPIC_API_KEY` অথবা `GEMINI_API_KEY` (config.yaml-এর `llm.provider` মিলিয়ে)।

**চালানো:** repo → Actions → *shorts-daily* → Run workflow। শুরুতে `count = 1`, `privacy = private` দিয়ে পরীক্ষা করুন।
প্রতিদিন সকাল ৮টায় (বাংলাদেশ) নিজে চলে, ১০টি ভিডিও বানায়।

**`config.yaml` → `upload.privacy`:**
- `private` — আপলোড হয়, শুধু আপনি দেখেন (শুরুতে নিরাপদ)।
- `public` — সঙ্গে সঙ্গে প্রকাশ।
- `schedule` — `slots`-এর সময়ে ছড়িয়ে প্রকাশ (প্রথম সময় আজ বাকি থাকলে আজ, নইলে কাল)।

**সতর্কতা:** নতুন API প্রজেক্টের আপলোড Google প্রথমে Private করে রাখে; Public করতে অডিট লাগে।
এমন হলে লগে ⚠ দেখাবে। আপলোড সীমা/কোটা শেষ হলে বট থামে, ভিডিওটি পরের দিন আবার চেষ্টা হয়।
`data/queue.jsonl` প্রতিবার রিপোতে সেভ হয়, তাই একই ভিডিও দুবার আপলোড হয় না।
