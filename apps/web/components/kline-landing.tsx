"use client";

import type { CSSProperties, FormEvent } from "react";
import { useEffect, useState } from "react";
import {
  ArrowRight,
  AtSign,
  BadgeCheck,
  CheckCircle2,
  BriefcaseBusiness,
  Camera,
  ClipboardCheck,
  Cpu,
  Database,
  Headphones,
  Layers3,
  LockKeyhole,
  Mail,
  MapPin,
  Menu,
  MessageCircle,
  MessagesSquare,
  Phone,
  PhoneCall,
  Send,
  ShieldCheck,
  Sparkles,
  Target,
  TrendingUp,
  Users,
  Wrench,
  X,
  Zap,
} from "lucide-react";
import type { LucideIcon } from "lucide-react";

const copy = {
  ru: {
    nav: [
      "Главная",
      "О компании",
      "Услуги",
      "Преимущества",
      "Кейсы",
      "Контакты",
    ],
    request: "Оставить заявку",
    login: "Войти",
    register: "Регистрация",
    eyebrow: "Аутсорсинг клиентского сервиса",
    heroTitle: "K-Line — аутсорсинговый Call Center",
    heroText: "Берём на себя звонки, заявки и поддержку клиентов 24/7.",
    outsourceTitle: "Аутсорсинговая команда",
    outsourceText: "Звонки · заявки · поддержка",
    consult: "Получить консультацию",
    online: "Команда уже на связи",
    onlineText: "Среднее время ответа — 18 секунд",
    sla: "SLA сегодня",
    secure: "Безопасность",
    secureText: "Данные под защитой",
    clientProof: "4.9 / 5 · 500+ клиентов доверяют нам",
    details: "Подробнее",
    aboutTag: "О компании",
    aboutTitle: "Надёжный партнёр для вашего бизнеса",
    aboutText:
      "K-Line — современный аутсорсинговый контакт-центр, который помогает бизнесу быстро отвечать клиентам, не терять заявки и повышать качество обслуживания.",
    aboutItems: [
      [
        "Опытные операторы",
        "Каждый специалист проходит обучение и регулярную оценку качества.",
      ],
      [
        "Современные технологии",
        "Объединяем телефонию, CRM и аналитику в единую систему.",
      ],
      [
        "Индивидуальный подход",
        "Настраиваем процессы, сценарии и отчётность под ваши задачи.",
      ],
    ],
    quality: "Контроль качества",
    qualityText: "100% обращений в единой системе",
    servicesTag: "Наши услуги",
    servicesTitle: "Комплексные решения для вашего бизнеса",
    servicesText:
      "Берём на себя все каналы коммуникации — от первого звонка до технической поддержки.",
    services: [
      [
        "Телефонная поддержка",
        "Входящие и исходящие линии с быстрым ответом и контролем качества.",
      ],
      ["Чат-поддержка", "Живое общение с клиентами на сайте и в мессенджерах."],
      [
        "Email-поддержка",
        "Точные, персональные ответы и соблюдение заданного SLA.",
      ],
      [
        "Телемаркетинг",
        "Продажи, квалификация лидов и возврат клиентов по вашим сценариям.",
      ],
      [
        "Опросы и исследования",
        "Собираем обратную связь и превращаем её в понятную аналитику.",
      ],
      [
        "Техническая поддержка",
        "Помогаем пользователям решать вопросы без лишних переключений.",
      ],
    ],
    stats: [
      ["2+", "лет опыта"],
      ["500+", "довольных клиентов"],
      ["100+", "операторов"],
      ["24/7", "поддержка клиентов"],
    ],
    whyTag: "Преимущества",
    whyTitle: "Почему выбирают нас",
    whyText:
      "Строим сервис на измеримых стандартах, прозрачной аналитике и бережном отношении к каждому клиенту.",
    why: [
      [
        "Качество обслуживания",
        "Контроль диалогов, обучение команды и понятные SLA для стабильного результата.",
      ],
      [
        "Технологии и CRM",
        "Интеграция с вашими системами, омниканальность и отчёты в реальном времени.",
      ],
      [
        "Безопасность данных",
        "Ролевой доступ, защищённая инфраструктура и строгие внутренние регламенты.",
      ],
      [
        "Ориентация на результат",
        "Фокусируемся на бизнес-метриках: конверсии, скорости и удовлетворённости.",
      ],
    ],
    casesTag: "Кейсы и результаты",
    casesTitle: "Цифры, которые говорят за нас",
    casesText:
      "Каждый проект начинается с целей бизнеса и заканчивается измеримым результатом.",
    cases: [
      [
        "−45%",
        "пропущенных звонков",
        "Ритейл",
        "Настроили резервную линию и прогноз нагрузки для сети магазинов.",
      ],
      [
        "+60%",
        "скорость обработки",
        "Сервисы",
        "Объединили заявки из телефона, сайта и мессенджеров в одном окне.",
      ],
      [
        "4.8/5",
        "оценка сервиса",
        "E-commerce",
        "Внедрили контроль качества и персональное обучение операторов.",
      ],
    ],
    result: "Результат",
    ctaTitle: "Готовы улучшить обслуживание ваших клиентов?",
    ctaText: "Оставьте заявку, и мы предложим решение под ваш бизнес.",
    contactTag: "Свяжитесь с нами",
    contactTitle: "Обсудим задачи вашего бизнеса",
    contactText:
      "Расскажите, что нужно улучшить. Мы изучим задачу и предложим подходящий формат команды.",
    contacts: ["Телефон", "Email", "Telegram", "WhatsApp", "Адрес"],
    city: "Ташкент, Узбекистан",
    formTitle: "Получить предложение",
    formText: "Ответим в течение рабочего дня.",
    name: "Имя",
    phone: "Телефон",
    email: "Email",
    comment: "Комментарий",
    namePh: "Как к вам обращаться?",
    phonePh: "+998 90 539 59 39",
    emailPh: "name@company.uz",
    commentPh: "Коротко расскажите о задаче",
    submit: "Отправить заявку",
    consent: "Нажимая кнопку, вы соглашаетесь с политикой конфиденциальности.",
    sent: "Спасибо! Заявка отправлена. Мы скоро свяжемся с вами.",
    footerText:
      "Аутсорсинговый контакт-центр, который превращает каждое обращение в хороший клиентский опыт.",
    navigation: "Навигация",
    footerServices: "Услуги",
    footerContact: "Контакты",
    privacy: "Политика конфиденциальности",
    rights: "Все права защищены.",
    privacyTitle: "Политика конфиденциальности",
    privacyBody:
      "K-Line использует данные из формы только для связи по вашей заявке и подготовки предложения. Мы не передаём контактные данные третьим лицам без законных оснований и храним их только в течение срока, необходимого для обработки обращения.",
    close: "Закрыть",
    processed: "Обращения обработаны",
  },
  qq: {
    nav: [
      "Bas bet",
      "Kompaniya haqqında",
      "Xızmetler",
      "Abzallıqlar",
      "Keysler",
      "Baylanıs",
    ],
    request: "Ótinim qaldırıw",
    login: "Kiriw",
    register: "Dizimnen ótiw",
    eyebrow: "Klient servisini autsorsing",
    heroTitle: "K-Line — autsorsing Call Center",
    heroText:
      "Qońırawlar, ótinimler hám klientlerdi 24/7 qollap-quwatlawdı ózimizge alamız.",
    outsourceTitle: "Autsorsing komandası",
    outsourceText: "Qońırawlar · ótinimler · qollap-quwatlaw",
    consult: "Másláhát alıw",
    online: "Komanda baylanısta",
    onlineText: "Ortasha juwap waqıtı — 18 sekund",
    sla: "Búgingi SLA",
    secure: "Qáwipsizlik",
    secureText: "Maǵlıwmatlar qorǵalǵan",
    clientProof: "4.9 / 5 · 500+ klient bizge isenedi",
    details: "Tolıǵıraq",
    aboutTag: "Kompaniya haqqında",
    aboutTitle: "Biznesińiz ushın isenimli sherik",
    aboutText:
      "K-Line — bizneske klientlerge tez juwap beriwge, ótinimlerdi joǵaltpawǵa hám xızmet sapasın arttırıwǵa járdem beretuǵın zamanagóy autsorsing kontakt-orayı.",
    aboutItems: [
      [
        "Tájiriybeli operatorlar",
        "Hár bir qánige oqıwdan hám sapası turaqlı bahalawdan ótedi.",
      ],
      [
        "Zamanagóy texnologiyalar",
        "Telefoniya, CRM hám analitikanı bir sistemaǵa birlestiremiz.",
      ],
      [
        "Jeke jantasıw",
        "Processlerdi, skriptlerdi hám esabatlardı wazıypalarıńızǵa beyimlestiremiz.",
      ],
    ],
    quality: "Sapa baqlawı",
    qualityText: "Múrájatlardıń 100% bir sistemada",
    servicesTag: "Xızmetlerimiz",
    servicesTitle: "Biznesińiz ushın kompleksli sheshimler",
    servicesText:
      "Birinshi qońırawdan texnikalıq járdemge shekem barlıq baylanıs kanalların basqaramız.",
    services: [
      [
        "Telefon arqalı qollap-quwatlaw",
        "Tez juwap hám sapa baqlawı menen kiriwshi hám shıǵıwshı liniyalar.",
      ],
      [
        "Chat-qollap-quwatlaw",
        "Saytta hám messenjerlerde klientler menen janlı baylanıs.",
      ],
      [
        "Email-qollap-quwatlaw",
        "Anıq, jeke juwaplar hám belgilengen SLAǵa ámel etiw.",
      ],
      ["Telemarketing", "Satıw, lidlerdi bahalaw hám klientlerdi qaytarıw."],
      [
        "Sorawnamalar hám izertlewler",
        "Keri baylanıstı jıynap, onı túsinikli analitikaǵa aylandırámız.",
      ],
      [
        "Texnikalıq qollap-quwatlaw",
        "Paydalanıwshılardıń sorawların artıqsha baǵdarlawsız sheshemiz.",
      ],
    ],
    stats: [
      ["2+", "jıllıq tájiriybe"],
      ["500+", "razı klient"],
      ["100+", "operator"],
      ["24/7", "klientlerdi qollap-quwatlaw"],
    ],
    whyTag: "Abzallıqlar",
    whyTitle: "Ne ushın bizdi tańlaydı",
    whyText:
      "Servisti ólshenetuǵın standartlar, ashıq analitika hám hár bir klientke itibar tiykarında quramız.",
    why: [
      [
        "Xızmet sapası",
        "Turaqlı nátiyje ushın sóylesiwlerdi baqlaw, komandanı oqıtıw hám anıq SLA.",
      ],
      [
        "Texnologiyalar hám CRM",
        "Sistemalarıńız benen integraciya, omnikanallıq hám real waqıttaǵı esabatlar.",
      ],
      [
        "Maǵlıwmatlar qáwipsizligi",
        "Róllik kiriw, qorǵalǵan infrastruktura hám qatań ishki qaǵıydalar.",
      ],
      [
        "Nátiyjege baǵdarlanıw",
        "Konversiya, tezlik hám klientlerdiń razılıǵı sıyaqlı biznes kórsetkishlerine itibar beremiz.",
      ],
    ],
    casesTag: "Keysler hám nátiyjeler",
    casesTitle: "Biz ushın sóyleytuǵın sanlar",
    casesText:
      "Hár bir joybar biznes maqsetlerinen baslanıp, ólshenetuǵın nátiyje menen juwmaqlanadı.",
    cases: [
      [
        "−45%",
        "ótkizip jiberilgen qońırawlar",
        "Retail",
        "Dúkanlar tarmaǵı ushın rezerv liniyanı hám júkleme boljawın sazladıq.",
      ],
      [
        "+60%",
        "qayta islew tezligi",
        "Xızmetler",
        "Telefon, sayt hám messenjerlerdegi ótinimlerdi bir aynada birlestirdik.",
      ],
      [
        "4.8/5",
        "servis bahası",
        "E-commerce",
        "Sapa baqlawın hám operatorlardı jeke oqıtıwdı engizdik.",
      ],
    ],
    result: "Nátiyje",
    ctaTitle: "Klientlerińizge xızmet kórsetiwdi jaqsılawǵa tayarsız ba?",
    ctaText: "Ótinim qaldırıń — biznesińizge say sheshim usınıs etemiz.",
    contactTag: "Biz benen baylanısıń",
    contactTitle: "Biznes wazıypalarıńızdı talqılaymız",
    contactText:
      "Neni jaqsılaw kerek ekenin aytıń. Wazıypanı úyrenip, komandanıń say formatın usınıs etemiz.",
    contacts: ["Telefon", "Email", "Telegram", "WhatsApp", "Mánzil"],
    city: "Tashkent, Ózbekstan",
    formTitle: "Usınıs alıw",
    formText: "Bir jumıs kúni ishinde juwap beremiz.",
    name: "Atıńız",
    phone: "Telefon",
    email: "Email",
    comment: "Túsinik",
    namePh: "Sizge qalay múrájat eteyik?",
    phonePh: "+998 90 539 59 39",
    emailPh: "name@company.uz",
    commentPh: "Wazıypa haqqında qısqasha jazıń",
    submit: "Ótinimdi jiberiw",
    consent: "Túymeni basıw arqalı qupıyalıq siyasatına razılıq bildiresiz.",
    sent: "Raxmet! Ótinim jiberildi. Tez arada siz benen baylanısamız.",
    footerText:
      "Hár bir múrájattı jaqsı klient tájiriybesine aylandıratuǵın autsorsing kontakt-orayı.",
    navigation: "Navigaciya",
    footerServices: "Xızmetler",
    footerContact: "Baylanıs",
    privacy: "Qupıyalıq siyasatı",
    rights: "Barlıq huqıqlar qorǵalǵan.",
    privacyTitle: "Qupıyalıq siyasatı",
    privacyBody:
      "K-Line formadaǵı maǵlıwmatlardan tek ótinimińiz boyınsha baylanısıw hám usınıs tayarlaw ushın paydalanadı. Biz baylanıs maǵlıwmatların nızamlı tiykarlarsız úshinshi táreplerge bermeymiz hám olardı múrájattı qayta islew ushın kerekli múddet dawamında saqlaymız.",
    close: "Jabıw",
    processed: "Qayta islengen múrájatlar",
  },
  uz: {
    nav: [
      "Bosh sahifa",
      "Kompaniya",
      "Xizmatlar",
      "Afzalliklar",
      "Natijalar",
      "Aloqa",
    ],
    request: "Ariza qoldirish",
    login: "Kirish",
    register: "Ro‘yxatdan o‘tish",
    eyebrow: "Mijozlar servisi autsorsingi",
    heroTitle: "K-Line — autsorsing Call Center",
    heroText:
      "Qo‘ng‘iroqlar, arizalar va mijozlar yordamini 24/7 o‘z zimmamizga olamiz.",
    outsourceTitle: "Autsorsing jamoasi",
    outsourceText: "Qo‘ng‘iroqlar · arizalar · yordam",
    consult: "Maslahat olish",
    online: "Jamoa aloqada",
    onlineText: "O‘rtacha javob vaqti — 18 soniya",
    sla: "Bugungi SLA",
    secure: "Xavfsizlik",
    secureText: "Ma’lumotlar himoyada",
    clientProof: "4.9 / 5 · 500+ mijoz bizga ishonadi",
    details: "Batafsil",
    aboutTag: "Kompaniya haqida",
    aboutTitle: "Biznesingiz uchun ishonchli hamkor",
    aboutText:
      "K-Line — mijozlarga tez javob berish, arizalarni yo‘qotmaslik va xizmat sifatini oshirishga yordam beradigan zamonaviy autsorsing aloqa markazi.",
    aboutItems: [
      [
        "Tajribali operatorlar",
        "Har bir mutaxassis ta’lim va muntazam sifat nazoratidan o‘tadi.",
      ],
      [
        "Zamonaviy texnologiyalar",
        "Telefoniya, CRM va tahlilni yagona tizimda birlashtiramiz.",
      ],
      [
        "Individual yondashuv",
        "Jarayonlar, skriptlar va hisobotlarni vazifangizga moslaymiz.",
      ],
    ],
    quality: "Sifat nazorati",
    qualityText: "100% murojaatlar yagona tizimda",
    servicesTag: "Xizmatlarimiz",
    servicesTitle: "Biznesingiz uchun kompleks yechimlar",
    servicesText:
      "Birinchi qo‘ng‘iroqdan texnik yordamgacha barcha aloqa kanallarini boshqaramiz.",
    services: [
      [
        "Telefon yordami",
        "Tez javob va sifat nazorati bilan kiruvchi va chiquvchi liniyalar.",
      ],
      ["Chat yordami", "Sayt va messenjerlarda mijozlar bilan jonli muloqot."],
      [
        "Email yordami",
        "Aniq, shaxsiy javoblar va belgilangan SLAga rioya qilish.",
      ],
      ["Telemarketing", "Sotuvlar, lidlarni saralash va mijozlarni qaytarish."],
      [
        "So‘rov va tadqiqotlar",
        "Fikr-mulohazalarni tushunarli tahlilga aylantiramiz.",
      ],
      [
        "Texnik yordam",
        "Foydalanuvchilar savollarini ortiqcha yo‘naltirishsiz hal qilamiz.",
      ],
    ],
    stats: [
      ["2+", "yillik tajriba"],
      ["500+", "mamnun mijoz"],
      ["100+", "operator"],
      ["24/7", "mijozlar yordami"],
    ],
    whyTag: "Afzalliklar",
    whyTitle: "Nega bizni tanlashadi",
    whyText:
      "Xizmatni o‘lchanadigan standartlar, shaffof tahlil va har bir mijozga e’tibor asosida quramiz.",
    why: [
      [
        "Xizmat sifati",
        "Barqaror natija uchun suhbat nazorati, jamoa ta’limi va aniq SLA.",
      ],
      [
        "Texnologiya va CRM",
        "Tizimlaringiz bilan integratsiya, omnikanallik va real vaqt hisobotlari.",
      ],
      [
        "Ma’lumotlar xavfsizligi",
        "Rolli kirish, himoyalangan infratuzilma va qat’iy ichki qoidalar.",
      ],
      [
        "Natijaga yo‘nalish",
        "Konversiya, tezlik va qoniqish kabi biznes ko‘rsatkichlariga e’tibor.",
      ],
    ],
    casesTag: "Natijalar",
    casesTitle: "Biz haqimizda gapiradigan raqamlar",
    casesText:
      "Har bir loyiha biznes maqsadlaridan boshlanib, o‘lchanadigan natija bilan yakunlanadi.",
    cases: [
      [
        "−45%",
        "o‘tkazib yuborilgan qo‘ng‘iroq",
        "Retail",
        "Do‘konlar tarmog‘i uchun zaxira liniya va yuklama prognozini sozladik.",
      ],
      [
        "+60%",
        "qayta ishlash tezligi",
        "Xizmatlar",
        "Telefon, sayt va messenjerlardagi arizalarni bir oynada birlashtirdik.",
      ],
      [
        "4.8/5",
        "xizmat bahosi",
        "E-commerce",
        "Sifat nazorati va operatorlarning shaxsiy ta’limini joriy etdik.",
      ],
    ],
    result: "Natija",
    ctaTitle: "Mijozlaringizga xizmat ko‘rsatishni yaxshilashga tayyormisiz?",
    ctaText: "Ariza qoldiring — biznesingizga mos yechim taklif qilamiz.",
    contactTag: "Biz bilan bog‘laning",
    contactTitle: "Biznes vazifalaringizni muhokama qilamiz",
    contactText:
      "Nimani yaxshilash kerakligini ayting. Vazifani o‘rganib, mos jamoa formatini taklif qilamiz.",
    contacts: ["Telefon", "Email", "Telegram", "WhatsApp", "Manzil"],
    city: "Toshkent, O‘zbekiston",
    formTitle: "Taklif olish",
    formText: "Bir ish kuni ichida javob beramiz.",
    name: "Ism",
    phone: "Telefon",
    email: "Email",
    comment: "Izoh",
    namePh: "Sizga qanday murojaat qilaylik?",
    phonePh: "+998 90 539 59 39",
    emailPh: "name@company.uz",
    commentPh: "Vazifa haqida qisqacha yozing",
    submit: "Ariza yuborish",
    consent: "Tugmani bosib, maxfiylik siyosatiga rozilik bildirasiz.",
    sent: "Rahmat! Ariza yuborildi. Tez orada siz bilan bog‘lanamiz.",
    footerText:
      "Har bir murojaatni yaxshi mijoz tajribasiga aylantiradigan autsorsing aloqa markazi.",
    navigation: "Navigatsiya",
    footerServices: "Xizmatlar",
    footerContact: "Aloqa",
    privacy: "Maxfiylik siyosati",
    rights: "Barcha huquqlar himoyalangan.",
    privacyTitle: "Maxfiylik siyosati",
    privacyBody:
      "K-Line formadagi ma’lumotlardan faqat arizangiz bo‘yicha bog‘lanish va taklif tayyorlash uchun foydalanadi. Biz aloqa ma’lumotlarini qonuniy asoslarsiz uchinchi shaxslarga bermaymiz va ularni faqat murojaatni ko‘rib chiqish uchun zarur muddat davomida saqlaymiz.",
    close: "Yopish",
    processed: "Qayta ishlangan murojaatlar",
  },
  kk: {
    nav: [
      "Басты бет",
      "Компания туралы",
      "Қызметтер",
      "Артықшылықтар",
      "Кейстер",
      "Байланыс",
    ],
    request: "Өтінім қалдыру",
    login: "Кіру",
    register: "Тіркелу",
    eyebrow: "Клиент сервисінің аутсорсингі",
    heroTitle: "K-Line — аутсорсинг Call Center",
    heroText:
      "Қоңырауларды, өтінімдерді және клиент қолдауын 24/7 өз мойнымызға аламыз.",
    outsourceTitle: "Аутсорсинг командасы",
    outsourceText: "Қоңырау · өтінім · қолдау",
    consult: "Кеңес алу",
    online: "Команда байланыста",
    onlineText: "Орташа жауап беру уақыты — 18 секунд",
    sla: "Бүгінгі SLA",
    secure: "Қауіпсіздік",
    secureText: "Деректер қорғалған",
    clientProof: "4.9 / 5 · 500+ клиент бізге сенеді",
    details: "Толығырақ",
    aboutTag: "Компания туралы",
    aboutTitle: "Бизнесіңіз үшін сенімді серіктес",
    aboutText:
      "K-Line — бизнеске клиенттерге жылдам жауап беруге, өтінімдерді жоғалтпауға және қызмет көрсету сапасын арттыруға көмектесетін заманауи аутсорсингтік байланыс орталығы.",
    aboutItems: [
      [
        "Тәжірибелі операторлар",
        "Әр маман оқытудан және тұрақты сапа бағалауынан өтеді.",
      ],
      [
        "Заманауи технологиялар",
        "Телефонияны, CRM мен аналитиканы бір жүйеге біріктіреміз.",
      ],
      [
        "Жеке тәсіл",
        "Процестерді, сценарийлерді және есептерді міндеттеріңізге бейімдейміз.",
      ],
    ],
    quality: "Сапаны бақылау",
    qualityText: "Өтінімдердің 100%-ы бір жүйеде",
    servicesTag: "Қызметтеріміз",
    servicesTitle: "Бизнесіңізге арналған кешенді шешімдер",
    servicesText:
      "Алғашқы қоңыраудан техникалық қолдауға дейін барлық байланыс арналарын басқарамыз.",
    services: [
      [
        "Телефон арқылы қолдау",
        "Жылдам жауап пен сапа бақылауы бар кіріс және шығыс желілері.",
      ],
      [
        "Чат арқылы қолдау",
        "Сайтта және мессенджерлерде клиенттермен тікелей байланыс.",
      ],
      [
        "Email арқылы қолдау",
        "Нақты, жеке жауаптар және белгіленген SLA талаптарын сақтау.",
      ],
      ["Телемаркетинг", "Сату, лидтерді іріктеу және клиенттерді қайтару."],
      [
        "Сауалнамалар мен зерттеулер",
        "Кері байланысты жинап, оны түсінікті аналитикаға айналдырамыз.",
      ],
      [
        "Техникалық қолдау",
        "Пайдаланушылардың мәселелерін артық бағыттаусыз шешеміз.",
      ],
    ],
    stats: [
      ["2+", "жылдық тәжірибе"],
      ["500+", "риза клиент"],
      ["100+", "оператор"],
      ["24/7", "клиенттерді қолдау"],
    ],
    whyTag: "Артықшылықтар",
    whyTitle: "Неліктен бізді таңдайды",
    whyText:
      "Сервисті өлшенетін стандарттар, ашық аналитика және әр клиентке мұқият көзқарас негізінде құрамыз.",
    why: [
      [
        "Қызмет көрсету сапасы",
        "Тұрақты нәтиже үшін диалогтарды бақылау, команданы оқыту және түсінікті SLA.",
      ],
      [
        "Технологиялар және CRM",
        "Жүйелеріңізбен интеграция, омниканалдық және нақты уақыттағы есептер.",
      ],
      [
        "Деректер қауіпсіздігі",
        "Рөлдік қолжетімділік, қорғалған инфрақұрылым және қатаң ішкі ережелер.",
      ],
      [
        "Нәтижеге бағдарлану",
        "Конверсия, жылдамдық және клиенттердің қанағаттануы сияқты бизнес көрсеткіштеріне назар аударамыз.",
      ],
    ],
    casesTag: "Кейстер мен нәтижелер",
    casesTitle: "Біз туралы сөйлейтін сандар",
    casesText:
      "Әр жоба бизнес мақсаттарынан басталып, өлшенетін нәтижемен аяқталады.",
    cases: [
      [
        "−45%",
        "өткізіп алынған қоңыраулар",
        "Ритейл",
        "Дүкендер желісі үшін резервтік желі мен жүктеме болжамын баптадық.",
      ],
      [
        "+60%",
        "өңдеу жылдамдығы",
        "Қызметтер",
        "Телефон, сайт және мессенджерлердегі өтінімдерді бір терезеге біріктірдік.",
      ],
      [
        "4.8/5",
        "сервис бағасы",
        "E-commerce",
        "Сапаны бақылау мен операторларды жеке оқытуды енгіздік.",
      ],
    ],
    result: "Нәтиже",
    ctaTitle: "Клиенттерге қызмет көрсетуді жақсартуға дайынсыз ба?",
    ctaText: "Өтінім қалдырыңыз — бизнесіңізге сәйкес шешім ұсынамыз.",
    contactTag: "Бізбен байланысыңыз",
    contactTitle: "Бизнес міндеттеріңізді талқылаймыз",
    contactText:
      "Нені жақсарту керегін айтыңыз. Міндетті зерттеп, команданың қолайлы форматын ұсынамыз.",
    contacts: ["Телефон", "Email", "Telegram", "WhatsApp", "Мекенжай"],
    city: "Ташкент, Өзбекстан",
    formTitle: "Ұсыныс алу",
    formText: "Бір жұмыс күні ішінде жауап береміз.",
    name: "Аты-жөніңіз",
    phone: "Телефон",
    email: "Email",
    comment: "Пікір",
    namePh: "Сізге қалай хабарласайық?",
    phonePh: "+998 90 539 59 39",
    emailPh: "name@company.uz",
    commentPh: "Міндет туралы қысқаша жазыңыз",
    submit: "Өтінім жіберу",
    consent: "Түймені басу арқылы құпиялық саясатымен келісесіз.",
    sent: "Рақмет! Өтінім жіберілді. Жақында сізбен байланысамыз.",
    footerText:
      "Әр өтінімді жақсы клиенттік тәжірибеге айналдыратын аутсорсингтік байланыс орталығы.",
    navigation: "Навигация",
    footerServices: "Қызметтер",
    footerContact: "Байланыс",
    privacy: "Құпиялық саясаты",
    rights: "Барлық құқықтар қорғалған.",
    privacyTitle: "Құпиялық саясаты",
    privacyBody:
      "K-Line формадағы деректерді тек өтініміңіз бойынша байланысу және ұсыныс дайындау үшін пайдаланады. Біз байланыс деректерін заңды негіздерсіз үшінші тұлғаларға бермейміз және оларды өтінімді өңдеуге қажетті мерзім ішінде ғана сақтаймыз.",
    close: "Жабу",
    processed: "Өңделген өтінімдер",
  },
};

const navIds = ["home", "about", "services", "advantages", "cases", "contact"];
const serviceIcons = [
  PhoneCall,
  MessagesSquare,
  Mail,
  TrendingUp,
  ClipboardCheck,
  Wrench,
];
const aboutIcons = [Users, Cpu, Layers3];
const whyIcons = [BadgeCheck, Database, ShieldCheck, Target];
type Language = keyof typeof copy;

const languageLabels: Record<Language, string> = {
  ru: "RU",
  uz: "UZ",
  kk: "KZ",
  qq: "QQ",
};
const pageTitles = {
  qq: "K-Line — biznes ushın kontakt-oray",
  ru: "K-Line — контакт-центр для бизнеса",
  uz: "K-Line — biznes uchun aloqa markazi",
  kk: "K-Line — бизнеске арналған байланыс орталығы",
};

function Logo({
  light = false,
  homeLabel = "Home",
}: {
  light?: boolean;
  homeLabel?: string;
}) {
  return (
    <a
      className={`logo ${light ? "logo--light" : ""}`}
      href="#home"
      aria-label={`K-Line — ${homeLabel}`}
    >
      <svg className="logo__mark" viewBox="0 0 74 58" aria-hidden="true">
        <defs>
          <linearGradient id="kline-wave" x1="0" y1="0" x2="1" y2="0">
            <stop offset="0" stopColor="#B80A1F" />
            <stop offset="1" stopColor="#E41739" />
          </linearGradient>
        </defs>
        <rect
          x="2"
          y="20"
          width="7"
          height="18"
          rx="3.5"
          fill="url(#kline-wave)"
        />
        <rect
          x="14"
          y="12"
          width="7"
          height="34"
          rx="3.5"
          fill="url(#kline-wave)"
        />
        <rect
          x="26"
          y="2"
          width="7"
          height="54"
          rx="3.5"
          fill="url(#kline-wave)"
        />
        <rect
          x="38"
          y="11"
          width="7"
          height="36"
          rx="3.5"
          fill="url(#kline-wave)"
        />
        <rect
          x="50"
          y="19"
          width="7"
          height="20"
          rx="3.5"
          fill="url(#kline-wave)"
        />
        <rect
          x="62"
          y="24"
          width="7"
          height="12"
          rx="3.5"
          fill="url(#kline-wave)"
        />
      </svg>
      <span className="logo__type">
        <span className="logo__word">
          K<span>-</span>Line
        </span>
        <small className="logo__tagline">CALL CENTER</small>
      </span>
    </a>
  );
}

function renderHeroTitle(title: string) {
  const accent = "Call Center";
  const index = title.indexOf(accent);

  if (index === -1) return title;

  const beforeAccent = title.slice(0, index);
  const dashIndex = beforeAccent.indexOf("—");

  return (
    <>
      {dashIndex > -1 ? (
        <>
          {beforeAccent.slice(0, dashIndex + 1)}
          <br className="hero-title-mobile-break" />
          {beforeAccent.slice(dashIndex + 1)}
        </>
      ) : (
        beforeAccent
      )}
      <br className="hero-title-mobile-break" />
      <span className="hero-title-accent">{accent}</span>
      <br className="hero-title-mobile-break" />
      {title.slice(index + accent.length)}
    </>
  );
}

function SectionHead({
  tag,
  title,
  text,
  center = false,
}: {
  tag: string;
  title: string;
  text?: string;
  center?: boolean;
}) {
  return (
    <div
      className={`section-head reveal ${center ? "section-head--center" : ""}`}
    >
      <span className="eyebrow eyebrow--green">{tag}</span>
      <h2>{title}</h2>
      {text && <p>{text}</p>}
    </div>
  );
}

export function KLineLanding() {
  const [lang, setLang] = useState<Language>("ru");
  const [menuOpen, setMenuOpen] = useState(false);
  const [privacyOpen, setPrivacyOpen] = useState(false);
  const t = copy[lang];

  useEffect(() => {
    if (typeof localStorage?.getItem !== "function") return;
    const savedLanguage = localStorage.getItem("kline-language");
    if (savedLanguage && savedLanguage in copy)
      setLang(savedLanguage as Language);
  }, []);

  useEffect(() => {
    document.documentElement.lang = lang;
    document.title = pageTitles[lang];
    if (typeof localStorage?.setItem === "function")
      localStorage.setItem("kline-language", lang);
  }, [lang]);

  useEffect(() => {
    if (!("IntersectionObserver" in window)) {
      document
        .querySelectorAll(".kline-site .reveal")
        .forEach((element) => element.classList.add("is-visible"));
      return;
    }
    const observer = new IntersectionObserver(
      (entries) => {
        entries.forEach(
          (entry) =>
            entry.isIntersecting && entry.target.classList.add("is-visible"),
        );
      },
      { threshold: 0.12 },
    );
    document
      .querySelectorAll(".kline-site .reveal")
      .forEach((el) => observer.observe(el));
    return () => observer.disconnect();
  }, [lang]);

  const goTo = () => setMenuOpen(false);
  const submit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    const message = [
      `${t.name}: ${String(form.get("name") ?? "")}`,
      `${t.phone}: ${String(form.get("phone") ?? "")}`,
      `${t.email}: ${String(form.get("email") ?? "")}`,
      `${t.comment}: ${String(form.get("comment") ?? "")}`,
    ].join("\n");
    window.open(
      `https://wa.me/998905395939?text=${encodeURIComponent(message)}`,
      "_blank",
      "noopener,noreferrer",
    );
  };

  const contactItems: [LucideIcon, string, string, string][] = [
    [Phone, t.contacts[0], "+998 90 539 59 39", "tel:+998905395939"],
    [
      AtSign,
      t.contacts[1],
      "hurlimankenesbaeva22@gmail.com",
      "mailto:hurlimankenesbaeva22@gmail.com",
    ],
    [Send, t.contacts[2], "@kline_support", "https://t.me/kline_support"],
    [
      MessageCircle,
      t.contacts[3],
      "+998 90 539 59 39",
      "https://wa.me/998905395939",
    ],
    [MapPin, t.contacts[4], t.city, "https://maps.google.com/?q=Tashkent"],
  ];

  return (
    <div className="kline-site">
      <header className="site-header">
        <div className="container header-inner">
          <Logo homeLabel={t.nav[0]} />
          <nav
            className={`main-nav ${menuOpen ? "is-open" : ""}`}
            aria-label={t.navigation}
          >
            {t.nav.map((item, i) => (
              <a key={navIds[i]} href={`#${navIds[i]}`} onClick={goTo}>
                {item}
              </a>
            ))}
            <div className="mobile-actions">
              <a className="mobile-login" href="/login" onClick={goTo}>
                {t.login}
              </a>
              <a className="btn btn--primary" href="/register" onClick={goTo}>
                {t.register}
              </a>
            </div>
          </nav>
          <div className="header-actions">
            <div className="lang-switch" aria-label="Language">
              {(Object.keys(languageLabels) as Language[]).map((item) => (
                <button
                  key={item}
                  className={lang === item ? "active" : ""}
                  onClick={() => setLang(item)}
                  aria-pressed={lang === item}
                >
                  {languageLabels[item]}
                </button>
              ))}
            </div>
            <a className="header-login" href="/login">
              {t.login}
            </a>
            <a className="btn btn--primary header-cta" href="/register">
              {t.register}
            </a>
            <button
              className="menu-toggle"
              onClick={() => setMenuOpen(!menuOpen)}
              aria-expanded={menuOpen}
              aria-label={menuOpen ? t.close : "Menu"}
            >
              {menuOpen ? <X /> : <Menu />}
            </button>
          </div>
        </div>
      </header>

      <main>
        <section className="hero" id="home">
          <div className="hero-orb hero-orb--one" />
          <div className="hero-orb hero-orb--two" />
          <div className="hero-lines" aria-hidden="true" />
          <div className="container hero-grid">
            <div className="hero-copy reveal is-visible">
              <h1>{renderHeroTitle(t.heroTitle)}</h1>
              <p>{t.heroText}</p>
              <div className="hero-actions">
                <a className="btn btn--primary btn--large" href="#contact">
                  {t.request}
                  <ArrowRight size={19} />
                </a>
                <a
                  className="btn btn--ghost btn--large"
                  href="tel:+998905395939"
                >
                  <Phone size={18} />
                  {t.consult}
                </a>
              </div>
            </div>
            <div className="hero-visual reveal is-visible">
              <div className="hero-image-card">
                <img
                  src="/images/kline-hero-outsourcing.png"
                  alt={`${t.outsourceTitle} — K-Line`}
                />
              </div>
            </div>
          </div>
          <a className="scroll-cue" href="#about" aria-label={t.details}>
            <span />
          </a>
        </section>

        <section className="section about" id="about">
          <div className="container about-grid">
            <div className="about-visual reveal">
              <div className="image-frame">
                <img
                  src="/images/kline-team.png"
                  alt={`${t.aboutTitle} — K-Line`}
                  loading="lazy"
                />
              </div>
              <div className="quality-card">
                <span>
                  <CheckCircle2 size={24} />
                </span>
                <div>
                  <strong>{t.quality}</strong>
                  <small>{t.qualityText}</small>
                </div>
              </div>
              <div className="pattern-dots" />
            </div>
            <div className="about-content">
              <SectionHead
                tag={t.aboutTag}
                title={t.aboutTitle}
                text={t.aboutText}
              />
              <div className="about-list">
                {t.aboutItems.map(([title, text], i) => {
                  const Icon = aboutIcons[i];
                  return (
                    <div className="about-item reveal" key={title}>
                      <span className="feature-icon">
                        <Icon size={23} />
                      </span>
                      <div>
                        <h3>{title}</h3>
                        <p>{text}</p>
                      </div>
                    </div>
                  );
                })}
              </div>
            </div>
          </div>
        </section>

        <section className="section section--soft" id="services">
          <div className="container">
            <SectionHead
              tag={t.servicesTag}
              title={t.servicesTitle}
              text={t.servicesText}
              center
            />
            <div className="services-grid">
              {t.services.map(([title, text], i) => {
                const Icon = serviceIcons[i];
                return (
                  <article
                    className="service-card reveal"
                    key={title}
                    style={{ "--delay": `${i * 60}ms` } as CSSProperties}
                  >
                    <span className="service-icon">
                      <Icon />
                    </span>
                    <h3>{title}</h3>
                    <p>{text}</p>
                    <a href="#contact">
                      {t.details} <ArrowRight size={16} />
                    </a>
                  </article>
                );
              })}
            </div>
          </div>
        </section>

        <section className="stats-band">
          <div className="stats-orb" />
          <div className="container stats-grid">
            {t.stats.map(([value, label], i) => (
              <div className="stat reveal" key={label}>
                <strong>{value}</strong>
                <span>{label}</span>
                {i < t.stats.length - 1 && <i />}
              </div>
            ))}
          </div>
        </section>

        <section className="section advantages" id="advantages">
          <div className="container advantages-grid">
            <div className="advantages-intro">
              <SectionHead tag={t.whyTag} title={t.whyTitle} text={t.whyText} />
              <div className="mini-dashboard reveal">
                <div className="dashboard-top">
                  <span>
                    <Sparkles size={17} /> K-Line analytics
                  </span>
                  <span className="live">
                    <i /> LIVE
                  </span>
                </div>
                <div className="bars">
                  <span style={{ height: "45%" }} />
                  <span style={{ height: "67%" }} />
                  <span style={{ height: "54%" }} />
                  <span style={{ height: "82%" }} />
                  <span style={{ height: "72%" }} />
                  <span style={{ height: "94%" }} />
                </div>
                <div className="dashboard-foot">
                  <span>{t.processed}</span>
                  <strong>
                    12 846 <em>+18%</em>
                  </strong>
                </div>
              </div>
            </div>
            <div className="advantages-list">
              {t.why.map(([title, text], i) => {
                const Icon = whyIcons[i];
                return (
                  <article className="advantage-card reveal" key={title}>
                    <span className="advantage-number">0{i + 1}</span>
                    <span className="advantage-icon">
                      <Icon />
                    </span>
                    <div>
                      <h3>{title}</h3>
                      <p>{text}</p>
                    </div>
                  </article>
                );
              })}
            </div>
          </div>
        </section>

        <section className="section section--soft cases" id="cases">
          <div className="container">
            <SectionHead
              tag={t.casesTag}
              title={t.casesTitle}
              text={t.casesText}
              center
            />
            <div className="cases-grid">
              {t.cases.map(([metric, label, sector, text], i) => (
                <article
                  className="case-card reveal"
                  key={metric + sector}
                  style={{ "--delay": `${i * 70}ms` } as CSSProperties}
                >
                  <div className="case-top">
                    <span className="case-icon">
                      {i === 0 ? (
                        <PhoneCall />
                      ) : i === 1 ? (
                        <Zap />
                      ) : (
                        <BadgeCheck />
                      )}
                    </span>
                    <span className="case-sector">{sector}</span>
                  </div>
                  <span className="case-label">{t.result}</span>
                  <strong className="case-metric">{metric}</strong>
                  <h3>{label}</h3>
                  <p>{text}</p>
                  <div className="case-line">
                    <span
                      style={{
                        width: i === 0 ? "72%" : i === 1 ? "87%" : "94%",
                      }}
                    />
                  </div>
                </article>
              ))}
            </div>
          </div>
        </section>

        <section className="cta-section">
          <div className="container">
            <div className="cta-box reveal">
              <div className="cta-grid" />
              <div className="cta-glow" />
              <div>
                <span className="eyebrow eyebrow--dark">K-Line · 24/7</span>
                <h2>{t.ctaTitle}</h2>
                <p>{t.ctaText}</p>
              </div>
              <a className="btn btn--light btn--large" href="#contact">
                {t.request}
                <ArrowRight size={19} />
              </a>
            </div>
          </div>
        </section>

        <section className="section contact" id="contact">
          <div className="container contact-grid">
            <div className="contact-info">
              <SectionHead
                tag={t.contactTag}
                title={t.contactTitle}
                text={t.contactText}
              />
              <div className="contact-list reveal">
                {contactItems.map(([Icon, label, value, href]) => (
                  <a
                    key={label}
                    href={href}
                    target={href.startsWith("http") ? "_blank" : undefined}
                    rel="noreferrer"
                  >
                    <span>
                      <Icon size={20} />
                    </span>
                    <div>
                      <small>{label}</small>
                      <strong>{value}</strong>
                    </div>
                  </a>
                ))}
              </div>
            </div>
            <form className="contact-form reveal" onSubmit={submit}>
              <div className="form-head">
                <div>
                  <h3>{t.formTitle}</h3>
                  <p>{t.formText}</p>
                </div>
                <span>
                  <Headphones />
                </span>
              </div>
              <label>
                {t.name}
                <input
                  name="name"
                  type="text"
                  placeholder={t.namePh}
                  required
                  autoComplete="name"
                />
              </label>
              <div className="field-row">
                <label>
                  {t.phone}
                  <input
                    name="phone"
                    type="tel"
                    placeholder={t.phonePh}
                    required
                    autoComplete="tel"
                  />
                </label>
                <label>
                  {t.email}
                  <input
                    name="email"
                    type="email"
                    placeholder={t.emailPh}
                    required
                    autoComplete="email"
                  />
                </label>
              </div>
              <label>
                {t.comment}
                <textarea name="comment" rows={4} placeholder={t.commentPh} />
              </label>
              <button className="btn btn--primary btn--full" type="submit">
                {t.submit}
                <ArrowRight size={18} />
              </button>
              <small className="consent">
                <LockKeyhole size={13} />
                {t.consent}
              </small>
            </form>
          </div>
        </section>
      </main>

      <footer className="footer">
        <div className="container footer-grid">
          <div className="footer-brand">
            <Logo light homeLabel={t.nav[0]} />
            <p>{t.footerText}</p>
            <div className="socials">
              <a href="https://t.me/kline_support" aria-label="Telegram">
                <Send size={18} />
              </a>
              <a href="https://instagram.com" aria-label="Instagram">
                <Camera size={18} />
              </a>
              <a href="https://linkedin.com" aria-label="LinkedIn">
                <BriefcaseBusiness size={18} />
              </a>
            </div>
          </div>
          <div>
            <h3>{t.navigation}</h3>
            {t.nav.slice(0, 5).map((item, i) => (
              <a key={item} href={`#${navIds[i]}`}>
                {item}
              </a>
            ))}
          </div>
          <div>
            <h3>{t.footerServices}</h3>
            {t.services.slice(0, 4).map(([item]) => (
              <a key={item} href="#services">
                {item}
              </a>
            ))}
          </div>
          <div>
            <h3>{t.footerContact}</h3>
            <a href="tel:+998905395939">+998 90 539 59 39</a>
            <a href="mailto:hurlimankenesbaeva22@gmail.com">
              hurlimankenesbaeva22@gmail.com
            </a>
            <span>{t.city}</span>
          </div>
        </div>
        <div className="container footer-bottom">
          <span>
            © {new Date().getFullYear()} K-Line. {t.rights}
          </span>
          <button className="privacy-link" onClick={() => setPrivacyOpen(true)}>
            {t.privacy}
          </button>
        </div>
      </footer>
      {privacyOpen && (
        <div
          className="modal-backdrop"
          role="presentation"
          onMouseDown={() => setPrivacyOpen(false)}
        >
          <div
            className="privacy-modal"
            role="dialog"
            aria-modal="true"
            aria-labelledby="privacy-title"
            onMouseDown={(event) => event.stopPropagation()}
          >
            <button
              className="modal-close"
              onClick={() => setPrivacyOpen(false)}
              aria-label={t.close}
            >
              <X size={20} />
            </button>
            <span className="feature-icon">
              <ShieldCheck size={23} />
            </span>
            <h2 id="privacy-title">{t.privacyTitle}</h2>
            <p>{t.privacyBody}</p>
            <button
              className="btn btn--primary"
              onClick={() => setPrivacyOpen(false)}
            >
              {t.close}
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
