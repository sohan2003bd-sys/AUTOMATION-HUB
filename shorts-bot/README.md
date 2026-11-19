# বাংলা Shorts Automation Hub

বাংলা YouTube Shorts-এর জন্য একটি অটোমেশন বট। এটি বিষয় বেছে নেয়, তথ্য যাচাই করে, স্ক্রিপ্ট ও বাংলা ভয়েস তৈরি করে, স্টক ফুটেজ/ছবি বসিয়ে ৯:১৬ ভিডিও রেন্ডার করে এবং চাইলে নির্দিষ্ট সময়ে YouTube-এ প্রকাশের জন্য শিডিউল করে।

## এই সংস্করণের মূল সুবিধা

- **দিনে ১০টি ভিডিওর পরিকল্পনা:** প্রতি ২ ঘণ্টায় ১টি করে, বাংলাদেশ সময় সকাল ০৬:৪৫ থেকে পরদিন রাত ১২:৪৫ (০০:৪৫) পর্যন্ত ১০টি YouTube প্রকাশ-স্লট। GitHub Actions-এর trigger best-effort; তাই trigger কিছুটা দেরিতে এলেও YouTube-এ প্রকাশের সময় `upload.slots` অনুযায়ী schedule হয়।
- **১০টি Gemini API key:** `GEMINI_API_KEY` এবং `GEMINI_API_KEY_2` থেকে `GEMINI_API_KEY_10` পর্যন্ত ১০টি আলাদা GitHub Secret। প্রতিটি নির্ধারিত ভিডিওর সব Gemini call একটি নির্দিষ্ট key-তে pinned থাকে; quota/error হলে একই ভিডিও অন্য key-তে সরে যায় না। একটি নির্দিষ্ট slot-এর key কাজ না করলে সেই রান ব্যর্থ হবে, পরের slot তার নিজস্ব key ব্যবহার করবে।
- **টপিকের বৈচিত্র্য:** সাম্প্রতিক সংবাদ শুধু `trending` ক্যাটাগরি থেকে আসে; দৈনিক সর্বোচ্চ ২টি নতুন সংবাদ-আইডিয়া তৈরি হয়। বাকি বিষয় স্বাস্থ্য, টাকা-পয়সা, প্রতারণা-সচেতনতা, আইন, বিজ্ঞান, প্রযুক্তি, শিক্ষা, পরিবেশ, ইতিহাস, সংস্কৃতি ও মনস্তত্ত্বসহ বিভিন্ন সময়-নিরপেক্ষ ক্যাটাগরি থেকে আসে। একই বা কাছাকাছি বিষয় ক্যাটাগরি ভেদেও আবার নেওয়া ঠেকাতে ইতিহাসের সঙ্গে মিল পরীক্ষা করা হয়।
- **মানুষের ফুটেজ সীমিত:** মানুষ পুরোপুরি নিষিদ্ধ নয়। স্ক্রিপ্টে অন্তত ৮০% দৃশ্য মানুষবিহীন রাখার নির্দেশ আছে; media config-এ প্রতি ভিডিওতে সর্বোচ্চ ১টি মানুষের দৃশ্য ও ২০% সীমা সেট করা। শিরোনাম/ট্যাগ এবং OpenCV face/person detection দিয়ে অপ্রাসঙ্গিক বা অতিরিক্ত মানুষ-থাকা ক্লিপ বাদ দেওয়ার চেষ্টা করা হয়। এই detection শতভাগ নিখুঁত নয়।
- **আরও বাস্তবসম্মত ভিজ্যুয়াল:** বাস্তব স্টক ভিডিওকে অগ্রাধিকার, দৃশ্যভেদে আলাদা visual query, ৮ ধরনের মসৃণ transition, ৬ ধরনের color look, হালকা animated camera pan/zoom, বাংলা subtitle এবং পরিষ্কার voice-over।
- **ফ্যাক্ট-চেক ও গবেষণা:** Gemini grounding চালু থাকলে স্ক্রিপ্ট তৈরির আগে প্রাসঙ্গিক তথ্য খোঁজে; ভুল/অনিশ্চিত বিষয় কমানোর চেষ্টা করে। তবু প্রকাশের আগে ভিডিও দেখা জরুরি।

## ১. ইনস্টল ও কনফিগার

Python 3.11+ এবং FFmpeg লাগবে। Ubuntu/GitHub Actions-এ বাংলা ফন্টের প্যাকেজও ইনস্টল করতে হবে:

```bash
pip install -r requirements.txt
sudo apt-get install -y ffmpeg fonts-noto-core libraqm0 libfribidi0
```

`config.yaml`-এ `llm.provider: gemini` রাখুন। নিচের ১০টি API key-কে GitHub repository → **Settings → Secrets and variables → Actions**-এ আলাদা Secret হিসেবে যোগ করুন:

```text
GEMINI_API_KEY
GEMINI_API_KEY_2
GEMINI_API_KEY_3
GEMINI_API_KEY_4
GEMINI_API_KEY_5
GEMINI_API_KEY_6
GEMINI_API_KEY_7
GEMINI_API_KEY_8
GEMINI_API_KEY_9
GEMINI_API_KEY_10
```

দশটি Secret-এ দশটি **স্বতন্ত্র key** দিন; একই key একাধিক নামে বসাবেন না। কোটা আলাদা রাখতে সম্ভব হলে আলাদা Google Cloud project থেকে key তৈরি করুন—একই project-এর key-তে project-level quota ভাগাভাগি হতে পারে। নির্ধারিত GitHub workflow ১০টি key-ই না পেলে ভিডিও তৈরি শুরু করবে না।

YouTube upload/schedule-এর জন্য আরও এই Secrets লাগবে:

```text
YT_CLIENT_ID
YT_CLIENT_SECRET
YT_REFRESH_TOKEN
```

বাস্তব stock footage/photo-এর জন্য অন্তত একটি media key দিন:

```text
PEXELS_API_KEY
PIXABAY_API_KEY
UNSPLASH_ACCESS_KEY
```

`config.yaml`-এ চ্যানেলের তথ্য, ভয়েস, ভিডিও স্টাইল, প্রকাশের গোপনীয়তা ও সময় পরিবর্তন করা যায়। ডিফল্ট `upload.privacy: schedule`; ভিডিও প্রথমে Private হিসেবে আপলোড হয়ে নির্দিষ্ট সময়ের জন্য schedule হয়।

## ২. প্রতিদিনের স্বয়ংক্রিয় রান

GitHub repository-তে `.github/workflows/shorts.yml` workflow সক্রিয় রাখুন। এটি প্রতি ২ ঘণ্টার block-এ কয়েকবার trigger হওয়ার চেষ্টা করে; গেট ধাপ একই block-এ একাধিক ভিডিও হওয়া ঠেকায়। বাংলাদেশ সময়ের ১০টি YouTube প্রকাশ-স্লট `config.yaml`-এ:

```yaml
upload:
  privacy: schedule
  timezone_offset_hours: 6
  slots: ["06:45", "08:45", "10:45", "12:45", "14:45", "16:45", "18:45", "20:45", "22:45", "00:45"]
```

GitHub scheduled workflow নির্দিষ্ট মিনিটে চালানোর নিশ্চয়তা দেয় না; প্ল্যাটফর্মের চাপ বা দেরিতে trigger হতে পারে। তাই workflow-তে retry trigger রাখা হয়েছে এবং YouTube-এ আলাদা publish time বসানো হয়। কোনো রান সম্পূর্ণ বাদ পড়লে সেদিন ১০টি ভিডিও হওয়ার নিশ্চয়তা নেই।

প্রথমে **Actions → shorts-daily → Run workflow** থেকে `count = 1` ও `privacy = private` দিয়ে পরীক্ষা করুন। ভিডিও ঠিক আছে নিশ্চিত হলে `config.yaml`-এর schedule mode ব্যবহার করুন। হাতে চালানোর `count` বাড়ালে প্রতিটি ভিডিও পরের key slot পাবে।

## ৩. লোকালি পরীক্ষা

```bash
python -m shorts doctor
python -m shorts demo --tts edge
python -m shorts ideas --per-category 5
python -m shorts make --count 1
python -m shorts status
```

`python -m shorts run --count 1` দিয়ে upload workflow-ও পরীক্ষা করা যায়; YouTube credentials লাগবে।

## ৪. গুরুত্বপূর্ণ সেটিং

| সেটিং | ডিফল্ট | কাজ |
|---|---:|---|
| `ideas.daily_news_max` | `2` | দিনে সর্বোচ্চ কতটি নতুন সংবাদ-আইডিয়া তৈরি হবে |
| `media.no_people` | `false` | `false` হলে সীমিত মানুষ-থাকা ফুটেজ অনুমোদিত |
| `media.face_check` | `true` | OpenCV দিয়ে মানুষ/মুখ শনাক্ত করার চেষ্টা |
| `media.people_max_scenes` | `1` | প্রতি ভিডিওতে সর্বোচ্চ মানুষের দৃশ্য |
| `media.people_max_ratio` | `0.20` | মানুষের দৃশ্যের সর্বোচ্চ অনুপাত |
| `video.motion` | `cinematic` | বাস্তব ফুটেজে হালকা চলমান crop/pan |
| `video.transition` | `0.42` | দৃশ্য বদলের মসৃণ সময় (সেকেন্ড) |
| `video.quality` | `high` | ১০৮০×১৯২০ vertical video |
| `limits.monthly_max` | `300` | মাসিক upload cap; `0` দিলে সীমা থাকবে না |

## ৫. নিজের ভিডিও স্মার্ট এডিট

```bash
python -m shorts edit input/video.mp4
```

GitHub Actions-এর `edit-my-video` workflow-তেও `inbox/`-এ ভিডিও দিয়ে চালানো যায়।

## ৬. সতর্কতা

- YouTube API project-এর upload audit/permission না থাকলে YouTube কোনো ভিডিওকে Private রাখতে পারে; workflow log দেখুন।
- Gemini key-তে quota শেষ হলে সেই slot-এর ভিডিও ব্যর্থ হতে পারে; pinned-key নীতির কারণে একই ভিডিও অন্য key-তে স্বয়ংক্রিয়ভাবে বদলায় না।
- OpenCV face/person detection অনুমানভিত্তিক এবং সব ছবি/ভিডিওতে মানুষ ধরতে পারে না। stock footage-এর লাইসেন্স/শর্ত নিজে যাচাই করুন।
- দৈনিক ১০টি workflow trigger GitHub-এর best-effort scheduling-এর ওপর নির্ভরশীল। কোড ১০টি target slot রাখে, কিন্তু GitHub বা YouTube-এর availability/limits-এর কারণে ১০টি প্রকাশের নিশ্চয়তা দিতে পারে না।
- AI ভুল তথ্য বা অপ্রাসঙ্গিক ফুটেজ দিতে পারে। প্রথমে অল্প ভিডিও তৈরি করে নিজে পর্যালোচনা করুন; একই ছাঁচের ভিডিও অতিরিক্ত প্রকাশ করবেন না।
