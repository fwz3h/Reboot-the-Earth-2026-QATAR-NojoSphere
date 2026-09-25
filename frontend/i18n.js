/* ==========================================================================
   i18n.js -- all the text of the website, in several languages.

   Why: many farmers do not read English. The whole interface, the numbers and
   the explanations should be readable in their own language, including
   right-to-left scripts.

   HOW TO USE IT (from app.js):
       t("form.submit")                       -> the text in the current language
       t("results.showing", {visible: 6, total: 20})
       setLanguage("ar")                      -> switch language and redraw
       applyTranslations()                    -> fill every element that has a
                                                 data-i18n="..." attribute

   HOW TO ADD A NEW LANGUAGE:
     1. Copy the whole "en" block below and rename it, e.g. "pt".
     2. Translate every value (keep the {placeholders} exactly as they are).
     3. Add the code to SUPPORTED with the language's own name.
     4. Add the same code to SUPPORTED_LANGUAGES in backend/explain.py, so the
        AI explanations and the fallback sentence are written in that language.
   ========================================================================== */

/* Everything in this file is wrapped in a function so that the names below
   (STRINGS, SUPPORTED, t, ...) stay private to this file. Without this, the
   short name `t` here would clash with `const t` in app.js, because both files
   share one global scope -- and the browser would refuse to run app.js. */
(function () {
  "use strict";

/* ---------------------------------------------------------------------------
   1. THE TEXT
   --------------------------------------------------------------------------- */
const STRINGS = {
  /* ---------------- English ---------------- */
  en: {
    "app.tagline": "Pick your land on the map. We'll suggest what to grow, how to grow it, and what it will cost.",
    "lang.label": "Language",
    "status.checking": "Checking backend…",
    "status.ok": "Backend connected",
    "status.offline": "Backend offline",
    "section1.title": "Click your land on the map",
    "section1.hint": "Click anywhere to drop a pin. A circle shows roughly how big your land is for the area you enter below.",
    "location.none": "No location selected yet.",
    "error.mapFailed": "The map could not load. It needs an internet connection, because the map pictures come from the internet.",
    "location.selected": "Selected: {lat}, {lon} — click again to move the pin.",
    "section2.title": "Tell us about your farm",
    "form.area.label": "Land area (hectares)",
    "form.area.hint": "1 hectare = 10,000 m² (about 1.5 football pitches).",
    "form.budget.label": "Budget (US dollars)",
    "form.budget.hint": "Your start-up money. Leave 0 for no limit.",
    "form.submit": "Recommend crops & systems",
    "form.working": "Working…",
    "section3.title": "What we recommend",
    "site.coords": "Coordinates",
    "site.temp": "Avg temperature",
    "site.rain": "Yearly rainfall",
    "site.solar": "Solar radiation",
    "site.ph": "Soil pH",
    "site.placeholderNote": "Note: live NASA / SoilGrids data was unavailable for this location, so placeholder climate values are being used.",
    "site.cacheNote": "Climate and soil data for this spot came from the local cache (site_cache.json).",
    "site.offlineNote": "The backend is offline, so these numbers were computed in your browser from the downloaded data pack for {place} ({distance} km from your pin).",
    "offline.explanation": "{crop} in a {system} scores {score} out of 100 here. Start-up cost about {capex}; payback: {payback}.",
    "results.showAll": "Show all options",
    "results.showTop": "Show top {n}",
    "results.showing": "Showing {visible} of {total} crop + system combinations, best first.",
    "loading.lookup": "Looking up climate and soil data for your pin (this can take a few seconds)…",
    "footer.disclaimer": "Mazraa — Reboot the Earth 2026, Challenge 1. Numbers shown are placeholders for testing, not yet validated for real investment decisions.",
    "metric.suitability": "Suitability score",
    "metric.yield": "Expected yield",
    "metric.water": "Water use",
    "metric.energy": "Energy use",
    "metric.capex": "CapEx (one-off)",
    "metric.opex": "OpEx (per year)",
    "metric.revenue": "Revenue (per year)",
    "metric.profit": "Profit (per year)",
    "metric.payback": "Payback time",
    "unit.yield": "t/yr",
    "unit.water": "m³/yr",
    "unit.energy": "kWh/yr",
    "payback.never": "Never",
    "payback.months": "{n} months",
    "payback.years": "{n} yr",
    "badge.withinBudget": "Within budget",
    "badge.overBudget": "Over budget by {amount}",
    "badge.profitable": "Profitable",
    "badge.losesMoney": "Loses money with these numbers",
    "badge.ai": "AI explanation",
    "error.pickLocation": "Please click a location on the map first (step 1).",
    "error.areaInvalid": "Please enter a land area larger than 0 hectares.",
    "error.fetchFailed": "Could not get recommendations: {message} — is the backend running? Start it with: python app.py inside the backend folder.",
    "error.badAnswer": "The backend sent an unexpected answer.",
  },

  /* ---------------- العربية (Arabic, right-to-left) ---------------- */
  ar: {
    "app.tagline": "اختر أرضك على الخريطة، وسنقترح عليك ماذا تزرع، وكيف تزرعه، وكم سيكلّفك.",
    "lang.label": "اللغة",
    "status.checking": "جارٍ التحقق من الخادم…",
    "status.ok": "الخادم متصل",
    "status.offline": "الخادم غير متصل",
    "section1.title": "اضغط على أرضك في الخريطة",
    "section1.hint": "اضغط في أي مكان لتحديد موقعك. الدائرة تُظهر تقريباً مساحة أرضك حسب الحجم الذي تدخله أدناه.",
    "location.none": "لم يتم تحديد أي موقع بعد.",
    "error.mapFailed": "تعذّر تحميل الخريطة. تحتاج إلى اتصال بالإنترنت لأن صور الخريطة تأتي من الإنترنت.",
    "location.selected": "الموقع المحدد: {lat}، {lon} — اضغط مرة أخرى لنقل الدبوس.",
    "section2.title": "أخبرنا عن مزرعتك",
    "form.area.label": "مساحة الأرض (هكتار)",
    "form.area.hint": "١ هكتار = ١٠٠٠٠ م² (حوالي ملعب كرة قدم ونصف).",
    "form.budget.label": "الميزانية (دولار أمريكي)",
    "form.budget.hint": "رأس المال المتاح للبدء. اترك 0 بدون حد أقصى.",
    "form.submit": "اقترح المحاصيل وأنظمة الزراعة",
    "form.working": "جارٍ العمل…",
    "section3.title": "توصياتنا لك",
    "site.coords": "الإحداثيات",
    "site.temp": "متوسط الحرارة",
    "site.rain": "الأمطار السنوية",
    "site.solar": "الإشعاع الشمسي",
    "site.ph": "حموضة التربة",
    "site.placeholderNote": "ملاحظة: بيانات ناسا و SoilGrids غير متاحة لهذا الموقع، لذلك تُستخدم قيم مناخية تقديرية.",
    "site.cacheNote": "بيانات المناخ والتربة لهذا الموقع محفوظة محلياً في ملف site_cache.json.",
    "site.offlineNote": "الخادم غير متصل، لذا حُسبت هذه الأرقام داخل متصفحك من حزمة البيانات المحمّلة للموقع {place} ({distance} كم عن دبوسك).",
    "offline.explanation": "{crop} بنظام {system} يحصل على {score} من 100 هنا. التكلفة الأولية نحو {capex}؛ الاسترداد: {payback}.",
    "results.showAll": "اعرض كل الخيارات",
    "results.showTop": "اعرض الأفضل {n}",
    "results.showing": "يتم عرض {visible} من {total} من تركيبات المحصول والنظام، الأفضل أولاً.",
    "loading.lookup": "جارٍ البحث عن بيانات المناخ والتربة لموقعك (قد يستغرق بضع ثوانٍ)…",
    "footer.disclaimer": "مزرعة — Reboot the Earth 2026، التحدي الأول. الأرقام المعروضة تقديرية للاختبار وليست بعد صالحة لقرارات استثمارية حقيقية.",
    "metric.suitability": "درجة الملاءمة",
    "metric.yield": "الإنتاج المتوقع",
    "metric.water": "استهلاك المياه",
    "metric.energy": "استهلاك الطاقة",
    "metric.capex": "التكلفة الرأسمالية (مرة واحدة)",
    "metric.opex": "التشغيل (سنوياً)",
    "metric.revenue": "الإيرادات (سنوياً)",
    "metric.profit": "الربح (سنوياً)",
    "metric.payback": "مدة الاسترداد",
    "unit.yield": "طن/سنة",
    "unit.water": "م³/سنة",
    "unit.energy": "كيلوواط ساعة/سنة",
    "payback.never": "لا يُسترد",
    "payback.months": "{n} شهر",
    "payback.years": "{n} سنة",
    "badge.withinBudget": "داخل الميزانية",
    "badge.overBudget": "يتجاوز الميزانية بـ {amount}",
    "badge.profitable": "مربح",
    "badge.losesMoney": "خاسر بهذه الأرقام",
    "badge.ai": "شرح بالذكاء الاصطناعي",
    "error.pickLocation": "الرجاء الضغط على موقع في الخريطة أولاً (الخطوة ١).",
    "error.areaInvalid": "الرجاء إدخال مساحة أكبر من ٠ هكتار.",
    "error.fetchFailed": "تعذّر الحصول على التوصيات: {message} — هل الخادم يعمل؟ شغّله بالأمر python app.py داخل مجلد backend.",
    "error.badAnswer": "أرسل الخادم رداً غير متوقع.",
  },

  /* ---------------- Français (French) ---------------- */
  fr: {
    "app.tagline": "Choisissez votre terrain sur la carte. Nous vous dirons quoi cultiver, comment le cultiver et combien cela coûtera.",
    "lang.label": "Langue",
    "status.checking": "Vérification du serveur…",
    "status.ok": "Serveur connecté",
    "status.offline": "Serveur hors ligne",
    "section1.title": "Cliquez sur votre terrain sur la carte",
    "section1.hint": "Cliquez n'importe où pour placer un repère. Le cercle montre environ la taille de votre terrain selon la surface saisie ci-dessous.",
    "location.none": "Aucun lieu sélectionné pour l'instant.",
    "error.mapFailed": "La carte n'a pas pu se charger. Elle a besoin d'une connexion internet.",
    "location.selected": "Sélectionné : {lat}, {lon} — cliquez à nouveau pour déplacer le repère.",
    "section2.title": "Parlez-nous de votre exploitation",
    "form.area.label": "Surface (hectares)",
    "form.area.hint": "1 hectare = 10 000 m² (environ 1,5 terrain de football).",
    "form.budget.label": "Budget (dollars US)",
    "form.budget.hint": "Votre argent de départ. Laissez 0 pour aucune limite.",
    "form.submit": "Recommander cultures et systèmes",
    "form.working": "Traitement…",
    "section3.title": "Nos recommandations",
    "site.coords": "Coordonnées",
    "site.temp": "Température moyenne",
    "site.rain": "Pluie annuelle",
    "site.solar": "Rayonnement solaire",
    "site.ph": "pH du sol",
    "site.placeholderNote": "Remarque : les données NASA / SoilGrids n'étaient pas disponibles ici, des valeurs de remplacement sont utilisées.",
    "site.cacheNote": "Les données de climat et de sol viennent de la mémoire locale (site_cache.json).",
    "site.offlineNote": "Le serveur est hors ligne : ces chiffres ont été calculés dans votre navigateur à partir du paquet téléchargé pour {place} ({distance} km de votre repère).",
    "offline.explanation": "{crop}, système « {system} » : {score} sur 100 ici. Investissement de départ : environ {capex} ; retour : {payback}.",
    "results.showAll": "Voir toutes les options",
    "results.showTop": "Voir les {n} meilleures",
    "results.showing": "{visible} options sur {total} affichées, les meilleures d'abord.",
    "loading.lookup": "Recherche des données de climat et de sol pour votre repère (cela peut prendre quelques secondes)…",
    "footer.disclaimer": "Mazraa — Reboot the Earth 2026, Défi 1. Les chiffres affichés sont provisoires, non encore validés pour une vraie décision d'investissement.",
    "metric.suitability": "Score d'adaptation",
    "metric.yield": "Rendement attendu",
    "metric.water": "Eau utilisée",
    "metric.energy": "Énergie utilisée",
    "metric.capex": "Investissement (une fois)",
    "metric.opex": "Charges (par an)",
    "metric.revenue": "Recettes (par an)",
    "metric.profit": "Bénéfice (par an)",
    "metric.payback": "Retour sur investissement",
    "unit.yield": "t/an",
    "unit.water": "m³/an",
    "unit.energy": "kWh/an",
    "payback.never": "Jamais",
    "payback.months": "{n} mois",
    "payback.years": "{n} ans",
    "badge.withinBudget": "Dans le budget",
    "badge.overBudget": "Dépasse le budget de {amount}",
    "badge.profitable": "Rentable",
    "badge.losesMoney": "Perte avec ces chiffres",
    "badge.ai": "Explication par IA",
    "error.pickLocation": "Cliquez d'abord sur un lieu sur la carte (étape 1).",
    "error.areaInvalid": "Saisissez une surface supérieure à 0 hectare.",
    "error.fetchFailed": "Impossible d'obtenir les recommandations : {message} — le serveur est-il lancé ? Lancez python app.py dans le dossier backend.",
    "error.badAnswer": "Le serveur a renvoyé une réponse inattendue.",
  },

  /* ---------------- Español (Spanish) ---------------- */
  es: {
    "app.tagline": "Elige tu terreno en el mapa. Te diremos qué cultivar, cómo cultivarlo y cuánto costará.",
    "lang.label": "Idioma",
    "status.checking": "Comprobando el servidor…",
    "status.ok": "Servidor conectado",
    "status.offline": "Servidor desconectado",
    "section1.title": "Haz clic en tu terreno en el mapa",
    "section1.hint": "Haz clic en cualquier lugar para poner un marcador. El círculo muestra el tamaño aproximado de tu terreno según la superficie que escribas abajo.",
    "location.none": "Aún no has elegido ningún lugar.",
    "error.mapFailed": "No se pudo cargar el mapa. Necesita conexión a internet.",
    "location.selected": "Elegido: {lat}, {lon} — haz clic otra vez para mover el marcador.",
    "section2.title": "Cuéntanos sobre tu finca",
    "form.area.label": "Superficie (hectáreas)",
    "form.area.hint": "1 hectárea = 10.000 m² (como 1,5 campos de fútbol).",
    "form.budget.label": "Presupuesto (dólares)",
    "form.budget.hint": "Tu dinero inicial. Deja 0 para no poner límite.",
    "form.submit": "Recomendar cultivos y sistemas",
    "form.working": "Trabajando…",
    "section3.title": "Nuestra recomendación",
    "site.coords": "Coordenadas",
    "site.temp": "Temperatura media",
    "site.rain": "Lluvia anual",
    "site.solar": "Radiación solar",
    "site.ph": "pH del suelo",
    "site.placeholderNote": "Aviso: los datos de NASA / SoilGrids no estaban disponibles aquí, así que se usan valores provisionales.",
    "site.cacheNote": "Los datos de clima y suelo vienen de la caché local (site_cache.json).",
    "site.offlineNote": "El servidor está desconectado, así que estas cifras se calcularon en tu navegador con el paquete descargado de {place} ({distance} km de tu marcador).",
    "offline.explanation": "{crop} con el sistema {system} obtiene {score} sobre 100 aquí. Inversión inicial: unos {capex} ; recuperación: {payback}.",
    "results.showAll": "Ver todas las opciones",
    "results.showTop": "Ver las {n} mejores",
    "results.showing": "Mostrando {visible} de {total} combinaciones, las mejores primero.",
    "loading.lookup": "Buscando datos de clima y suelo para tu marcador (puede tardar unos segundos)…",
    "footer.disclaimer": "Mazraa — Reboot the Earth 2026, Reto 1. Las cifras mostradas son provisionales, aún no válidas para decisiones reales de inversión.",
    "metric.suitability": "Puntuación de idoneidad",
    "metric.yield": "Producción prevista",
    "metric.water": "Agua usada",
    "metric.energy": "Energía usada",
    "metric.capex": "Inversión (una vez)",
    "metric.opex": "Gastos (por año)",
    "metric.revenue": "Ingresos (por año)",
    "metric.profit": "Beneficio (por año)",
    "metric.payback": "Retorno de la inversión",
    "unit.yield": "t/año",
    "unit.water": "m³/año",
    "unit.energy": "kWh/año",
    "payback.never": "Nunca",
    "payback.months": "{n} meses",
    "payback.years": "{n} años",
    "badge.withinBudget": "Dentro del presupuesto",
    "badge.overBudget": "Excede el presupuesto en {amount}",
    "badge.profitable": "Rentable",
    "badge.losesMoney": "Pierde dinero con estas cifras",
    "badge.ai": "Explicación con IA",
    "error.pickLocation": "Primero haz clic en un lugar del mapa (paso 1).",
    "error.areaInvalid": "Escribe una superficie mayor de 0 hectáreas.",
    "error.fetchFailed": "No se pudieron obtener recomendaciones: {message} — ¿está encendido el servidor? Ejecuta python app.py en la carpeta backend.",
    "error.badAnswer": "El servidor envió una respuesta inesperada.",
  },

  /* ---------------- Kiswahili (Swahili) ---------------- */
  sw: {
    "app.tagline": "Chagua ardhi yako kwenye ramani. Tutakushauri mmea wa kupanda, jinsi ya kuupanda, na gharama yake.",
    "lang.label": "Lugha",
    "status.checking": "Inaangalia seva…",
    "status.ok": "Seva imeunganishwa",
    "status.offline": "Seva haipatikani",
    "section1.title": "Bofya ardhi yako kwenye ramani",
    "section1.hint": "Bofya mahali popote kuweka alama. Mviringo unaonyesha ukubwa wa ardhi yako kulingana na eneo unaloandika hapa chini.",
    "location.none": "Hakuna mahali palipochaguliwa bado.",
    "error.mapFailed": "Ramani haikupakiwa. Inahitaji mtandao kwa sababu picha za ramani zinatoka kwenye mtandao.",
    "location.selected": "Umechagua: {lat}, {lon} — bofya tena kuhamisha alama.",
    "section2.title": "Tuambie kuhusu shamba lako",
    "form.area.label": "Ukubwa wa ardhi (hekta)",
    "form.area.hint": "Hekta 1 = mita za mraba 10,000 (kama viwanja viwili vya mpira).",
    "form.budget.label": "Bajeti (dola za Marekani)",
    "form.budget.hint": "Pesa yako ya kuanzia. Weka 0 kama huna kikomo.",
    "form.submit": "Pendekeza mimea na mifumo",
    "form.working": "Inafanya kazi…",
    "section3.title": "Mapendekezo yetu",
    "site.coords": "Kurati",
    "site.temp": "Joto la wastani",
    "site.rain": "Mvua kwa mwaka",
    "site.solar": "Mionzi ya jua",
    "site.ph": "PH ya udongo",
    "site.placeholderNote": "Kumbuka: data ya NASA / SoilGrids haipatikani hapa, kwa hiyo tunatumia namba za kukisia.",
    "site.cacheNote": "Data ya hali ya hewa na udongo imetolewa kwenye kumbukumbu ya kompyuta (site_cache.json).",
    "site.offlineNote": "Seva haipatikani, kwa hizi namba zimehesabiwa kwenye kivinjari chako kutoka kwenye paketi iliyopakuliwa ya {place} (km {distance} kutoka alama yako).",
    "offline.explanation": "{crop} kwa mfumo wa {system} inapata {score} kati ya 100 hapa. Gharama ya kuanzisha: takriban {capex} ; marejesho: {payback}.",
    "results.showAll": "Onyesha chaguo zote",
    "results.showTop": "Onyesha {n} bora",
    "results.showing": "Inaonyesha {visible} kati ya {total} mchanganyiko, bora kwanza.",
    "loading.lookup": "Inatafuta data ya hali ya hewa na udongo (inaweza kuchukua sekunde chache)…",
    "footer.disclaimer": "Mazraa — Reboot the Earth 2026, Changamoto 1. Namba zilizoonyeshwa ni za majaribio, bado hazijathibitishwa kwa maamuzi ya kweli ya uwekezaji.",
    "metric.suitability": "Alama ya kufaa",
    "metric.yield": "Mavuno yanayotarajiwa",
    "metric.water": "Maji yanayotumika",
    "metric.energy": "Umeme unaotumika",
    "metric.capex": "Gharama ya kuanzisha (mara moja)",
    "metric.opex": "Gharama za mwaka",
    "metric.revenue": "Mapato kwa mwaka",
    "metric.profit": "Faida kwa mwaka",
    "metric.payback": "Muda wa kurejesha mtaji",
    "unit.yield": "t/mwaka",
    "unit.water": "m³/mwaka",
    "unit.energy": "kWh/mwaka",
    "payback.never": "Hairejeshwi",
    "payback.months": "miezi {n}",
    "payback.years": "miaka {n}",
    "badge.withinBudget": "Ndani ya bajeti",
    "badge.overBudget": "Inazidi bajeti kwa {amount}",
    "badge.profitable": "Ina faida",
    "badge.losesMoney": "Inapoteza pesa kwa namba hizi",
    "badge.ai": "Maelezo ya AI",
    "error.pickLocation": "Tafadhali bofya mahali kwenye ramani kwanza (hatua 1).",
    "error.areaInvalid": "Tafadhali weka eneo kubwa kuliko hekta 0.",
    "error.fetchFailed": "Imeshindikana kupata mapendekezo: {message} — seva inafanya kazi? Endesha python app.py ndani ya folda ya backend.",
    "error.badAnswer": "Seva ilituma jibu lisilotarajiwa.",
  },

  /* ---------------- हिन्दी (Hindi) ---------------- */
  hi: {
    "app.tagline": "नक्शे पर अपनी ज़मीन चुनें। हम बताएँगे कि क्या उगाएँ, कैसे उगाएँ और कितना खर्च आएगा।",
    "lang.label": "भाषा",
    "status.checking": "सर्वर की जाँच हो रही है…",
    "status.ok": "सर्वर जुड़ा है",
    "status.offline": "सर्वर बंद है",
    "section1.title": "नक्शे पर अपनी ज़मीन पर क्लिक करें",
    "section1.hint": "कहीं भी क्लिक करके पिन लगाएँ। गोला दिखाता है कि नीचे दर्ज क्षेत्रफल के हिसाब से आपकी ज़मीन कितनी बड़ी है।",
    "location.none": "अभी कोई जगह नहीं चुनी गई।",
    "error.mapFailed": "नक्शा लोड नहीं हो सका। इसके लिए इंटरनेट ज़रूरी है, क्योंकि नक्शे की तस्वीरें इंटरनेट से आती हैं।",
    "location.selected": "चुना गया: {lat}, {lon} — पिन हटाने के लिए दोबारा क्लिक करें।",
    "section2.title": "अपने खेत के बारे में बताएँ",
    "form.area.label": "ज़मीन का क्षेत्रफल (हेक्टेयर)",
    "form.area.hint": "1 हेक्टेयर = 10,000 वर्ग मीटर (लगभग डेढ़ फुटबॉल मैदान)।",
    "form.budget.label": "बजट (अमेरिकी डॉलर)",
    "form.budget.hint": "शुरू करने का पैसा। कोई सीमा न हो तो 0 लिखें।",
    "form.submit": "फसल और प्रणाली सुझाएँ",
    "form.working": "काम चल रहा है…",
    "section3.title": "हमारी सिफ़ारिश",
    "site.coords": "निर्देशांक",
    "site.temp": "औसत तापमान",
    "site.rain": "सालाना बारिश",
    "site.solar": "सौर विकिरण",
    "site.ph": "मिट्टी का pH",
    "site.placeholderNote": "ध्यान दें: इस जगह के लिए NASA / SoilGrids का डेटा नहीं मिला, इसलिए अनुमानित जलवायु आँकड़े इस्तेमाल हो रहे हैं।",
    "site.cacheNote": "जलवायु और मिट्टी का डेटा स्थानीय कैश (site_cache.json) से लिया गया है।",
    "site.offlineNote": "सर्वर ऑफ़लाइन है, इसलिए ये आँकड़े आपके ब्राउज़र में {place} के डाउनलोड किए गए डेटा पैक से गणना किए गए हैं (आपके पिन से {distance} किमी)।",
    "offline.explanation": "{system} प्रणाली में {crop} को यहाँ 100 में से {score} अंक मिलते हैं। शुरुआती लागत: लगभग {capex} ; वसूली: {payback}।",
    "results.showAll": "सभी विकल्प दिखाएँ",
    "results.showTop": "ऊपर के {n} दिखाएँ",
    "results.showing": "{total} में से {visible} विकल्प दिख रहे हैं, सबसे अच्छा पहले।",
    "loading.lookup": "आपके पिन के लिए जलवायु और मिट्टी का डेटा खोजा जा रहा है (कुछ सेकंड लग सकते हैं)…",
    "footer.disclaimer": "Mazraa — Reboot the Earth 2026, चुनौती 1। दिखाए गए आँकड़े परीक्षण के लिए अनुमानित हैं, असली निवेश के लिए मान्य नहीं।",
    "metric.suitability": "उपयुक्तता स्कोर",
    "metric.yield": "अनुमानित उपज",
    "metric.water": "पानी का उपयोग",
    "metric.energy": "ऊर्जा का उपयोग",
    "metric.capex": "शुरुआती लागत (एक बार)",
    "metric.opex": "सालाना खर्च",
    "metric.revenue": "सालाना कमाई",
    "metric.profit": "सालाना मुनाफ़ा",
    "metric.payback": "लागत वसूली का समय",
    "unit.yield": "टन/साल",
    "unit.water": "m³/साल",
    "unit.energy": "kWh/साल",
    "payback.never": "कभी नहीं",
    "payback.months": "{n} महीने",
    "payback.years": "{n} साल",
    "badge.withinBudget": "बजट के अंदर",
    "badge.overBudget": "बजट से {amount} ज़्यादा",
    "badge.profitable": "मुनाफ़े में",
    "badge.losesMoney": "इन आँकड़ों में घाटा",
    "badge.ai": "AI द्वारा व्याख्या",
    "error.pickLocation": "पहले नक्शे पर जगह चुनें (चरण 1)।",
    "error.areaInvalid": "कृपया 0 हेक्टेयर से बड़ा क्षेत्रफल डालें।",
    "error.fetchFailed": "सिफ़ारिशें नहीं मिल सकीं: {message} — सर्वर चल रहा है? backend फ़ोल्डर में python app.py चलाएँ।",
    "error.badAnswer": "सर्वर ने अनपेक्षित जवाब भेजा।",
  },

  /* ---------------- اردو (Urdu, right-to-left) ---------------- */
  ur: {
    "app.tagline": "نقشے پر اپنی زمین منتخب کریں۔ ہم بتائیں گے کہ کیا اگائیں، کیسے اگائیں اور خرچ کتنا ہوگا۔",
    "lang.label": "زبان",
    "status.checking": "سرور چیک ہو رہا ہے…",
    "status.ok": "سرور جڑ گیا",
    "status.offline": "سرور بند ہے",
    "section1.title": "نقشے پر اپنی زمین پر کلک کریں",
    "section1.hint": "کہیں بھی کلک کر کے پن لگائیں۔ دائرہ دکھاتا ہے کہ نیچے درج رقبے کے مطابق آپ کی زمین کتنی بڑی ہے۔",
    "location.none": "ابھی کوئی جگہ منتخب نہیں کی گئی۔",
    "error.mapFailed": "نقشہ لوڈ نہیں ہو سکا۔ اس کے لیے انٹرنیٹ ضروری ہے، کیونکہ نقشے کی تصویریں انٹرنیٹ سے آتی ہیں۔",
    "location.selected": "منتخب شدہ: {lat}، {lon} — پن ہٹانے کے لیے دوبارہ کلک کریں۔",
    "section2.title": "اپنے کھیت کے بارے میں بتائیں",
    "form.area.label": "زمین کا رقبہ (ہیکٹر)",
    "form.area.hint": "1 ہیکٹر = 10,000 مربع میٹر (تقریباً ڈیڑھ فٹبال میدان)۔",
    "form.budget.label": "بجٹ (امریکی ڈالر)",
    "form.budget.hint": "شروع کرنے کا پیسہ۔ کوئی حد نہ ہو تو 0 لکھیں۔",
    "form.submit": "فصل اور نظام تجویز کریں",
    "form.working": "کام جاری ہے…",
    "section3.title": "ہماری سفارش",
    "site.coords": "نقاط",
    "site.temp": "اوسط درجہ حرارت",
    "site.rain": "سالانہ بارش",
    "site.solar": "شمسی تابکاری",
    "site.ph": "مٹی کا pH",
    "site.placeholderNote": "نوٹ: اس جگہ کے لیے NASA / SoilGrids کا ڈیٹا دستیاب نہیں تھا، اس لیے تخمینی اعداد استعمال ہو رہے ہیں۔",
    "site.cacheNote": "موسمی اور مٹی کا ڈیٹا مقامی کیش (site_cache.json) سے لیا گیا ہے۔",
    "site.offlineNote": "سرور آف لائن ہے، اس لیے یہ اعداد آپ کے براؤزر میں {place} کے ڈاؤن لوڈ شدہ ڈیٹا پیک سے حساب کیے گئے ہیں (آپ کے پن سے {distance} کلومیٹر)۔",
    "offline.explanation": "{system} نظام میں {crop} کو یہاں 100 میں سے {score} ملتے ہیں۔ ابتدائی لاگت: تقریباً {capex} ؛ واپسی: {payback}۔",
    "results.showAll": "تمام آپشن دکھائیں",
    "results.showTop": "بہترین {n} دکھائیں",
    "results.showing": "{total} میں سے {visible} آپشن دکھائے جا رہے ہیں، بہترین پہلے۔",
    "loading.lookup": "آپ کے پن کے لیے موسم اور مٹی کا ڈیٹا تلاش کیا جا رہا ہے (چند سیکنڈ لگ سکتے ہیں)…",
    "footer.disclaimer": "مزرعہ — Reboot the Earth 2026، چیلنج 1۔ دکھائے گئے اعداد آزمائشی ہیں، حقیقی سرمایہ کاری کے لیے مستند نہیں۔",
    "metric.suitability": "موزونیت کا اسکور",
    "metric.yield": "متوقع پیداوار",
    "metric.water": "پانی کا استعمال",
    "metric.energy": "توانائی کا استعمال",
    "metric.capex": "ابتدائی لاگت (ایک بار)",
    "metric.opex": "سالانہ اخراجات",
    "metric.revenue": "سالانہ آمدنی",
    "metric.profit": "سالانہ منافع",
    "metric.payback": "لاگت کی واپسی کا وقت",
    "unit.yield": "ٹن/سال",
    "unit.water": "m³/سال",
    "unit.energy": "kWh/سال",
    "payback.never": "کبھی نہیں",
    "payback.months": "{n} ماہ",
    "payback.years": "{n} سال",
    "badge.withinBudget": "بجٹ کے اندر",
    "badge.overBudget": "بجٹ سے {amount} زیادہ",
    "badge.profitable": "نفع بخش",
    "badge.losesMoney": "ان اعداد میں نقصان",
    "badge.ai": "AI وضاحت",
    "error.pickLocation": "پہلے نقشے پر جگہ منتخب کریں (مرحلہ 1)۔",
    "error.areaInvalid": "براہ کرم 0 ہیکٹر سے بڑا رقبہ درج کریں۔",
    "error.fetchFailed": "سفارشات حاصل نہیں ہو سکیں: {message} — سرور چل رہا ہے؟ backend فولڈر میں python app.py چلائیں۔",
    "error.badAnswer": "سرور نے غیر متوقع جواب بھیجا۔",
  },
};

/* ---------------------------------------------------------------------------
   2. WHICH LANGUAGES ARE OFFERED
   ---------------------------------------------------------------------------
   "name" is written in the language's own script, so a farmer can recognise
   their own language even if the page is currently in English.
   "rtl" means the language is written right-to-left, so the whole page flips.
   "locale" is used only for number formats (thousand separators, decimals).
   The "-u-nu-latn" part asks browsers to keep 1 2 3 digits instead of Arabic
   or Devanagari numerals; farmers here read prices written with 1 2 3.
   --------------------------------------------------------------------------- */
const SUPPORTED = [
  { code: "en", name: "English",  locale: "en-US",       rtl: false },
  { code: "ar", name: "العربية",  locale: "ar-u-nu-latn", rtl: true  },
  { code: "fr", name: "Français", locale: "fr-FR",       rtl: false },
  { code: "es", name: "Español",  locale: "es-ES",       rtl: false },
  { code: "sw", name: "Kiswahili", locale: "sw-KE",      rtl: false },
  { code: "hi", name: "हिन्दी",   locale: "hi-IN",       rtl: false },
  { code: "ur", name: "اردو",     locale: "ur-u-nu-latn", rtl: true  },
];

const DEFAULT_LANGUAGE = "en";
const STORAGE_KEY = "mazraa_language";

/* ---------------------------------------------------------------------------
   3. STATE
   --------------------------------------------------------------------------- */
let currentLanguage = DEFAULT_LANGUAGE;

// Listeners that want to know when the language changes (app.js uses this to
// redraw the cards and to ask the backend for explanations in the new language).
const changeListeners = [];

/* ---------------------------------------------------------------------------
   4. PUBLIC FUNCTIONS
   --------------------------------------------------------------------------- */

/** The two-letter code of the language currently in use, e.g. "ar". */
function getLanguage() {
  return currentLanguage;
}

/** The full language record: {code, name, locale, rtl}. */
function getLanguageInfo(code) {
  return SUPPORTED.find((language) => language.code === (code || currentLanguage))
    || SUPPORTED[0];
}

/** True when the current language is written right-to-left. */
function isRtl() {
  return getLanguageInfo().rtl;
}

/** The locale string used for number formatting, e.g. "fr-FR". */
function getLocale() {
  return getLanguageInfo().locale;
}

/**
 * Look up a piece of text.
 *
 *   t("form.submit")
 *   t("results.showing", {visible: 6, total: 20})
 *
 * Falls back to English (and then to the key itself) so a missing translation
 * shows English text instead of breaking the page.
 */
function t(key, vars) {
  const table = STRINGS[currentLanguage] || {};
  let text = table[key];
  if (text === undefined) text = STRINGS[DEFAULT_LANGUAGE][key];
  if (text === undefined) {
    console.warn("[i18n] Missing translation for:", key);
    return key;
  }
  if (vars) {
    // Replace every {placeholder} with the value we were given.
    Object.keys(vars).forEach((name) => {
      text = text.split("{" + name + "}").join(String(vars[name]));
    });
  }
  return text;
}

/**
 * Switch language: remember the choice, flip the page direction if needed and
 * tell everyone who is listening.
 */
function setLanguage(code) {
  if (!STRINGS[code]) {
    console.warn("[i18n] Unknown language, keeping the current one:", code);
    return;
  }
  currentLanguage = code;

  // Persist the choice so it is remembered the next time the page is opened.
  try {
    window.localStorage.setItem(STORAGE_KEY, code);
  } catch (error) {
    // Private browsing can refuse localStorage. Not a problem.
  }

  const info = getLanguageInfo(code);
  document.documentElement.lang = info.code;
  document.documentElement.dir = info.rtl ? "rtl" : "ltr";

  applyTranslations();
  changeListeners.forEach((listener) => listener(code));
}

/**
 * Fill in every element that carries a data-i18n attribute:
 *
 *   <p data-i18n="app.tagline"></p>
 *   <input data-i18n-placeholder="form.budget.label">
 *   <button data-i18n-aria-label="lang.label">
 *
 * Everything else in the page is redrawn by app.js when the language changes.
 */
function applyTranslations(root) {
  const scope = root || document;

  scope.querySelectorAll("[data-i18n]").forEach((node) => {
    node.textContent = t(node.getAttribute("data-i18n"));
  });

  scope.querySelectorAll("[data-i18n-placeholder]").forEach((node) => {
    node.placeholder = t(node.getAttribute("data-i18n-placeholder"));
  });

  // A few elements (like the language picker) need a spoken label, not text.
  scope.querySelectorAll("[data-i18n-aria-label]").forEach((node) => {
    node.setAttribute("aria-label", t(node.getAttribute("data-i18n-aria-label")));
  });
}

/** Register a function to run whenever the language changes. */
function onLanguageChange(listener) {
  changeListeners.push(listener);
}

/**
 * Work out which language to start in:
 *   1. the one the farmer chose last time (saved in the browser)
 *   2. otherwise, the browser's own language if we support it
 *   3. otherwise English
 */
function detectInitialLanguage() {
  let saved = null;
  try {
    saved = window.localStorage.getItem(STORAGE_KEY);
  } catch (error) {
    saved = null;
  }
  if (saved && STRINGS[saved]) return saved;

  // navigator.languages is a list like ["ar-EG", "ar", "en-US"].
  const browserLanguages = navigator.languages || [navigator.language || "en"];
  for (const tag of browserLanguages) {
    const short = String(tag).toLowerCase().split("-")[0];
    if (STRINGS[short]) return short;
  }
  return DEFAULT_LANGUAGE;
}

/**
 * Build the language menu in the page header and start everything up.
 * Called once by app.js.
 */
function initI18n() {
  const select = document.getElementById("language-select");
  if (select) {
    select.innerHTML = "";
    SUPPORTED.forEach((language) => {
      const option = document.createElement("option");
      option.value = language.code;
      // Show "العربية (Arabic)" so both readers can find their language.
      option.textContent = language.code === "en"
        ? language.name
        : language.name + " (" + language.code + ")";
      select.appendChild(option);
    });
    select.addEventListener("change", (event) => setLanguage(event.target.value));
  }

  setLanguage(detectInitialLanguage());

  if (select) select.value = currentLanguage;
}

// Make the helpers available to app.js. This is the ONLY global this file
// creates (everything else is private to the function above).
window.MazraaI18N = {
  t,
  setLanguage,
  getLanguage,
  getLanguageInfo,
  getLocale,
  isRtl,
  applyTranslations,
  onLanguageChange,
  initI18n,
  SUPPORTED,
};

})();
