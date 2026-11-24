const $ = id => document.getElementById(id);
document.querySelectorAll('nav button').forEach(button => {

    button.onclick = () => {

        document.querySelectorAll('nav button')
            .forEach(b => b.classList.remove('active'));

        document.querySelectorAll('.tab')
            .forEach(t => t.classList.remove('active'));

        button.classList.add('active');

        $(button.dataset.tab).classList.add('active');

        if (button.dataset.tab === 'explore') {
            loadReports();
        }

        if (button.dataset.tab === 'alerts') {
            loadAlerts();
        }
    };

});

$('date').value = new Date().toISOString().slice(0, 10);

const translations = {

    en: {
        reportLangLabel: "Page language",
        reportTitle: "Report a crop issue",
        reportDescription: "Describe what you observe. Reports are observations, not confirmed diagnoses.",
        audioLabel: "Upload farmer voice recording",
        audioLangLabel: "Audio language",
        transcribeBtn: "Transcribe audio",
        originalLabel: "Original observation / transcript",
        listenTranscriptBtn: "Listen to transcription",
        languageLabel: "Original language",
        translateBtn: "Translate and extract report details",
        translatedLabel: "English translation",
        extractionTitle: "Extracted report details",
        extractionDescription: "These fields are automatically extracted from the English translation. Review and correct them before submitting.",
        cropLabel: "Crop",
        diseaseLabel: "Suspected disease / pest",
        symptomsLabel: "Symptoms / observations",
        locationLabel: "Region / village",
        dateLabel: "Report date",
        submitBtn: "Save report",

        originalPlaceholder: "The transcribed observation will appear here",
        translatedPlaceholder: "English translation will appear here",
        autoPlaceholder: "Automatically extracted",

        exploreLangLabel: "Page language",
        exploreTitle: "Explore community reports",
        fCropLabel: "Crop",
        fLocationLabel: "Region",
        fSymptomsLabel: "Symptoms",
        fCropPlaceholder: "Filter crop",
        fLocationPlaceholder: "Filter region",
        fSymptomsPlaceholder: "Filter symptoms",
        searchBtn: "Search",
        clearBtn: "Clear filters",

        symptomsDisplay: "Symptoms:",
        suspectedDisplay: "Suspected:",
        originalDisplay: "Original:",
        englishDisplay: "English:",
        notSpecified: "Not specified",
        noReports: "No reports found."
    },

    ta: {

        // REPORT PAGE
        reportLangLabel: "பக்க மொழி",
        reportTitle: "பயிர் பாதிப்பு அறிக்கை",
        reportDescription: "நீங்கள் கவனித்தவற்றை விவரிக்கவும். இவை கண்காணிப்புகள் மட்டுமே; உறுதிப்படுத்தப்பட்ட நோயறிதல்கள் அல்ல.",
        audioLabel: "விவசாயியின் குரல் பதிவைப் பதிவேற்றவும்",
        audioLangLabel: "ஒலிப்பதிவின் மொழி",
        transcribeBtn: "ஒலியை உரையாக மாற்றவும்",
        originalLabel: "அசல் கவனிப்பு / உரை",
        listenTranscriptBtn: "உரையைக் கேளுங்கள்",
        languageLabel: "அசல் மொழி",
        translateBtn: "மொழிபெயர்த்து அறிக்கை விவரங்களைப் பெறவும்",
        translatedLabel: "ஆங்கில மொழிபெயர்ப்பு",
        extractionTitle: "பெறப்பட்ட அறிக்கை விவரங்கள்",
        extractionDescription: "ஆங்கில மொழிபெயர்ப்பிலிருந்து இந்த விவரங்கள் தானாகப் பெறப்பட்டுள்ளன. சமர்ப்பிக்கும் முன் சரிபார்த்து திருத்தவும்.",
        cropLabel: "பயிர்",
        diseaseLabel: "சந்தேகிக்கப்படும் நோய் / பூச்சி",
        symptomsLabel: "அறிகுறிகள் / கவனிப்புகள்",
        locationLabel: "பகுதி / கிராமம்",
        dateLabel: "அறிக்கை தேதி",
        submitBtn: "அறிக்கையைச் சேமிக்கவும்",

        originalPlaceholder: "மாற்றிய உரை இங்கே தோன்றும்",
        translatedPlaceholder: "ஆங்கில மொழிபெயர்ப்பு இங்கே தோன்றும்",
        autoPlaceholder: "தானாகப் பெறப்படும்",

        exploreLangLabel: "பக்க மொழி",
        exploreTitle: "சமூக அறிக்கைகளைப் பார்க்கவும்",
        fCropLabel: "பயிர்",
        fLocationLabel: "பகுதி",
        fSymptomsLabel: "அறிகுறிகள்",
        fCropPlaceholder: "பயிரைத் தேடவும்",
        fLocationPlaceholder: "பகுதியைத் தேடவும்",
        fSymptomsPlaceholder: "அறிகுறிகளைத் தேடவும்",
        searchBtn: "தேடவும்",
        clearBtn: "வடிகட்டிகளை அழிக்கவும்",

        symptomsDisplay: "அறிகுறிகள்:",
        suspectedDisplay: "சந்தேகிக்கப்படும் நோய்:",
        originalDisplay: "அசல்:",
        englishDisplay: "ஆங்கிலம்:",
        notSpecified: "குறிப்பிடப்படவில்லை",
        noReports: "அறிக்கைகள் எதுவும் இல்லை."
    },

    te: {
        reportLangLabel: "పేజీ భాష",
        reportTitle: "పంట సమస్యను నివేదించండి",
        reportDescription: "మీరు గమనించిన విషయాలను వివరించండి. ఇవి పరిశీలనలు మాత్రమే; నిర్ధారించిన రోగ నిర్ధారణలు కావు.",
        audioLabel: "రైతు వాయిస్ రికార్డింగ్‌ను అప్‌లోడ్ చేయండి",
        audioLangLabel: "ఆడియో భాష",
        transcribeBtn: "ఆడియోను లిప్యంతరీకరించండి",
        originalLabel: "అసలు పరిశీలన / లిప్యంతరీకరణ",
        listenTranscriptBtn: "లిప్యంతరీకరణ వినండి",
        languageLabel: "అసలు భాష",
        translateBtn: "అనువదించి నివేదిక వివరాలను పొందండి",
        translatedLabel: "ఆంగ్ల అనువాదం",
        extractionTitle: "సేకరించిన నివేదిక వివరాలు",
        extractionDescription: "ఈ వివరాలు ఆంగ్ల అనువాదం నుండి స్వయంచాలకంగా తీసుకోబడ్డాయి. సమర్పించే ముందు పరిశీలించి సరిచేయండి.",
        cropLabel: "పంట",
        diseaseLabel: "అనుమానిత వ్యాధి / పురుగు",
        symptomsLabel: "లక్షణాలు / పరిశీలనలు",
        locationLabel: "ప్రాంతం / గ్రామం",
        dateLabel: "నివేదిక తేదీ",
        submitBtn: "నివేదికను సేవ్ చేయండి",

        originalPlaceholder: "లిప్యంతరీకరణ ఇక్కడ కనిపిస్తుంది",
        translatedPlaceholder: "ఆంగ్ల అనువాదం ఇక్కడ కనిపిస్తుంది",
        autoPlaceholder: "స్వయంచాలకంగా పొందబడుతుంది",

        exploreLangLabel: "పేజీ భాష",
        exploreTitle: "సమాజ నివేదికలను చూడండి",
        fCropLabel: "పంట",
        fLocationLabel: "ప్రాంతం",
        fSymptomsLabel: "లక్షణాలు",
        fCropPlaceholder: "పంటను వెతకండి",
        fLocationPlaceholder: "ప్రాంతాన్ని వెతకండి",
        fSymptomsPlaceholder: "లక్షణాలను వెతకండి",
        searchBtn: "వెతకండి",
        clearBtn: "ఫిల్టర్‌లను తొలగించండి",

        symptomsDisplay: "లక్షణాలు:",
        suspectedDisplay: "అనుమానిత వ్యాధి:",
        originalDisplay: "అసలు:",
        englishDisplay: "ఆంగ్లం:",
        notSpecified: "పేర్కొనలేదు",
        noReports: "నివేదికలు కనుగొనబడలేదు."
    },

    hi: {
        reportLangLabel: "पृष्ठ की भाषा",
        reportTitle: "फसल की समस्या की रिपोर्ट करें",
        reportDescription: "आपने जो देखा है उसका वर्णन करें। ये अवलोकन हैं, पुष्टि किए गए निदान नहीं।",
        audioLabel: "किसान की आवाज़ की रिकॉर्डिंग अपलोड करें",
        audioLangLabel: "ऑडियो की भाषा",
        transcribeBtn: "ऑडियो को लिखित रूप में बदलें",
        originalLabel: "मूल विवरण / प्रतिलेखन",
        listenTranscriptBtn: "प्रतिलेखन सुनें",
        languageLabel: "मूल भाषा",
        translateBtn: "अनुवाद करें और रिपोर्ट विवरण निकालें",
        translatedLabel: "अंग्रेज़ी अनुवाद",
        extractionTitle: "निकाले गए रिपोर्ट विवरण",
        extractionDescription: "ये विवरण अंग्रेज़ी अनुवाद से स्वचालित रूप से निकाले गए हैं। जमा करने से पहले समीक्षा और सुधार करें।",
        cropLabel: "फसल",
        diseaseLabel: "संदिग्ध रोग / कीट",
        symptomsLabel: "लक्षण / अवलोकन",
        locationLabel: "क्षेत्र / गाँव",
        dateLabel: "रिपोर्ट की तारीख",
        submitBtn: "रिपोर्ट सहेजें",

        originalPlaceholder: "प्रतिलेखन यहाँ दिखाई देगा",
        translatedPlaceholder: "अंग्रेज़ी अनुवाद यहाँ दिखाई देगा",
        autoPlaceholder: "स्वचालित रूप से निकाला गया",

        exploreLangLabel: "पृष्ठ की भाषा",
        exploreTitle: "सामुदायिक रिपोर्ट देखें",
        fCropLabel: "फसल",
        fLocationLabel: "क्षेत्र",
        fSymptomsLabel: "लक्षण",
        fCropPlaceholder: "फसल खोजें",
        fLocationPlaceholder: "क्षेत्र खोजें",
        fSymptomsPlaceholder: "लक्षण खोजें",
        searchBtn: "खोजें",
        clearBtn: "फ़िल्टर हटाएँ",

        symptomsDisplay: "लक्षण:",
        suspectedDisplay: "संदिग्ध रोग:",
        originalDisplay: "मूल:",
        englishDisplay: "अंग्रेज़ी:",
        notSpecified: "निर्दिष्ट नहीं",
        noReports: "कोई रिपोर्ट नहीं मिली।"
    }

};

function applyLanguage(language) {

    const t = translations[language] || translations.en;

    if ($('reportLang')) {
        $('reportLang').value = language;
    }

    if ($('exploreLang')) {
        $('exploreLang').value = language;
    }

    const textFields = [

        'reportLangLabel',
        'reportTitle',
        'reportDescription',
        'audioLabel',
        'audioLangLabel',
        'transcribeBtn',
        'originalLabel',
        'listenTranscriptBtn',
        'languageLabel',
        'translateBtn',
        'translatedLabel',
        'extractionTitle',
        'extractionDescription',
        'cropLabel',
        'diseaseLabel',
        'symptomsLabel',
        'locationLabel',
        'dateLabel',
        'submitBtn',

        'exploreLangLabel',
        'exploreTitle',
        'fCropLabel',
        'fLocationLabel',
        'fSymptomsLabel',
        'searchBtn',
        'clearBtn'

    ];

    textFields.forEach(id => {

        if ($(id) && t[id]) {
            $(id).textContent = t[id];
        }

    });

    if ($('original')) {
        $('original').placeholder = t.originalPlaceholder;
    }

    if ($('translated')) {
        $('translated').placeholder = t.translatedPlaceholder;
    }

    ['crop', 'disease', 'symptoms', 'location']
        .forEach(id => {

            if ($(id)) {
                $(id).placeholder = t.autoPlaceholder;
            }

        });

    if ($('fCrop')) {
        $('fCrop').placeholder = t.fCropPlaceholder;
    }

    if ($('fLocation')) {
        $('fLocation').placeholder = t.fLocationPlaceholder;
    }

    if ($('fSymptoms')) {
        $('fSymptoms').placeholder = t.fSymptomsPlaceholder;
    }

    loadReports();

}

$('reportLang').onchange = () => {

    applyLanguage($('reportLang').value);

};

$('exploreLang').onchange = () => {

    applyLanguage($('exploreLang').value);

};

applyLanguage('en');

async function request(url, options = {}) {

    const r = await fetch(url, options);

    const data = await r.json().catch(() => ({}));

    if (!r.ok) {
        throw Error(data.detail || 'Request failed');
    }

    return data;

}

$('audioLang').onchange = () => {

    const selected = $('audioLang').value;

    if (selected) {
        $('language').value = selected;
    }

};

$('transcribeBtn').onclick = async () => {

    const file = $('audio').files[0];

    if (!file) {
        return alert('Choose an audio file first');
    }

    const formData = new FormData();

    formData.append('file', file);

    const selectedLanguage = $('audioLang').value;

    $('transcribeBtn').disabled = true;

    $('audioMsg').textContent = 'Transcribing audio...';

    try {

        const result = await request(
            '/api/transcribe?language=' +
            encodeURIComponent(selectedLanguage),
            {
                method: 'POST',
                body: formData
            }
        );

        $('original').value = result.text;

        if (selectedLanguage) {
            $('language').value = selectedLanguage;
        }

        $('transcriptAudio').pause();
        $('transcriptAudio').src = '';
        $('transcriptAudio').style.display = 'none';

        $('translated').value = '';
        $('crop').value = '';
        $('disease').value = '';
        $('symptoms').value = '';
        $('location').value = '';

        $('audioMsg').textContent =
            'Transcription ready. Review or listen before translating.';

    } catch (e) {

        $('audioMsg').textContent = e.message;

    } finally {

        $('transcribeBtn').disabled = false;

    }

};

$('listenTranscriptBtn').onclick = async () => {

    const text = $('original').value.trim();

    if (!text) {
        alert('Transcribe or enter the observation first.');
        return;
    }

    const language = $('language').value;

    $('listenTranscriptBtn').disabled = true;

    $('audioMsg').textContent = 'Generating speech...';

    try {

        const response = await fetch('/api/speak', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            },
            body: JSON.stringify({
                text: text,
                language: language
            })
        });

        if (!response.ok) {

            const error = await response.json().catch(() => ({}));

            throw new Error(error.detail || 'Speak API failed');

        }

        const blob = await response.blob();

        if (blob.size === 0) {
            throw new Error('The Speak API returned empty audio.');
        }

        const url = URL.createObjectURL(blob);

        const player = $('transcriptAudio');

        player.pause();
        player.src = url;
        player.style.display = 'block';

        player.load();

        await player.play();

        $('audioMsg').textContent = 'Playing transcription.';

    } catch (e) {

        console.error("Speak playback error:", e);

        $('audioMsg').textContent = 'Playback error: ' + e.message;

    } finally {

        $('listenTranscriptBtn').disabled = false;

    }

};

$('translateBtn').onclick = async () => {

    const originalText = $('original').value.trim();

    if (!originalText) {
        return alert('Enter or transcribe the observation first');
    }

    $('translateBtn').disabled = true;

    $('audioMsg').textContent = 'Translating to English...';

    try {

        const translation = await request('/api/translate', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            },
            body: JSON.stringify({
                text: originalText,
                target_language: 'en'
            })
        });

        $('translated').value = translation.translation;

        $('audioMsg').textContent =
            'Extracting crop, disease, symptoms and region...';

        const details = await request('/api/extract', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            },
            body: JSON.stringify({
                text: translation.translation
            })
        });

        $('crop').value = details.crop || 'Unknown';

        $('disease').value =
            details.suspected_disease || 'Unknown';

        $('symptoms').value = details.symptoms || 'Unknown';

        $('location').value = details.region || 'Unknown';

        $('audioMsg').textContent =
            'Translation and extraction complete. Review the fields before submitting.';

    } catch (e) {

        $('audioMsg').textContent = e.message;

    } finally {

        $('translateBtn').disabled = false;

    }

};

$('submitBtn').onclick = async () => {

    const payload = {

        crop: $('crop').value.trim(),

        symptoms: $('symptoms').value.trim(),

        suspected_disease: $('disease').value.trim(),

        original_language: $('language').value,

        original_text: $('original').value.trim(),

        translated_text: $('translated').value.trim(),

        location: $('location').value.trim(),

        date: $('date').value

    };

    const requiredFields = [
        payload.crop,
        payload.symptoms,
        payload.location,
        payload.original_text,
        payload.translated_text
    ];

    if (requiredFields.some(field => !field)) {

        $('submitMsg').textContent =
            'Complete transcription, translation and extracted details first.';

        return;

    }

    $('submitBtn').disabled = true;

    $('submitMsg').textContent = 'Saving report...';

    try {

        const result = await request('/api/reports', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            },
            body: JSON.stringify(payload)
        });

        $('submitMsg').textContent =
            'Report saved successfully (ID ' +
            result.id +
            '). Check Explore reports and the NGO dashboard.';

        [
            'original',
            'translated',
            'crop',
            'disease',
            'symptoms',
            'location'
        ].forEach(id => {
            $(id).value = '';
        });

        $('transcriptAudio').pause();
        $('transcriptAudio').src = '';
        $('transcriptAudio').style.display = 'none';

        $('audio').value = '';

        $('audioMsg').textContent = '';

        if ($('explore').classList.contains('active')) {
            loadReports();
        }

    } catch (e) {

        $('submitMsg').textContent = e.message;

    } finally {

        $('submitBtn').disabled = false;

    }

};

$('searchBtn').onclick = loadReports;

$('clearBtn').onclick = () => {

    ['fCrop', 'fLocation', 'fSymptoms']
        .forEach(id => $(id).value = '');

    loadReports();

};

$('refreshAlerts').onclick = loadAlerts;

async function loadReports() {

    try {

        const params = new URLSearchParams({

            crop: $('fCrop')?.value || '',

            location: $('fLocation')?.value || '',

            symptoms: $('fSymptoms')?.value || ''

        });

        const data = await request('/api/reports?' + params);

        const lang = $('reportLang')?.value || 'en';

        const t = translations[lang] || translations.en;

        $('reports').innerHTML = data.length

            ? data.map(report => `

                <article class="report">

                    <h3>
                        ${esc(report.crop)} · ${esc(report.location)}
                    </h3>

                    <span class="tag">
                        ${esc(report.date)}
                    </span>

                    <span class="tag">
                        ${esc(report.original_language)}
                    </span>

                    <p>
                        <b>${esc(t.symptomsDisplay)}</b>
                        ${esc(report.symptoms)}
                    </p>

                    <p>
                        <b>${esc(t.suspectedDisplay)}</b>
                        ${esc(report.suspected_disease || t.notSpecified)}
                    </p>

                    <p>
                        <b>${esc(t.originalDisplay)}</b>
                        ${esc(report.original_text)}
                    </p>

                    <p>
                        <b>${esc(t.englishDisplay)}</b>
                        ${esc(report.translated_text)}
                    </p>

                </article>

            `).join('')

            : `<p>${esc(t.noReports)}</p>`;

    } catch (e) {

        $('reports').textContent = e.message;

    }

}

async function loadAlerts() {

    try {

        const data = await request('/api/alerts');

        $('alertList').innerHTML = data.length

            ? data.map(alertItem => `

                <article class="alert">

                    <h3>Potential regional pattern</h3>

                    <p>
                        <b>Region:</b>
                        ${esc(alertItem.region)}
                        ·
                        <b>Crop:</b>
                        ${esc(alertItem.crop)}
                    </p>

                    <p>
                        <b>Matching reports:</b>
                        ${alertItem.report_count}
                    </p>

                    <p>
                        <b>Symptoms group:</b>
                        ${esc(alertItem.symptoms_key)}
                    </p>

                    <p class="status">
                        Status: ${esc(alertItem.status)}
                    </p>

                    <p class="small">
                        This is a heuristic signal, not a confirmed outbreak.
                    </p>

                    <select id="status-${alertItem.id}">

                        <option>Pending</option>

                        <option>Under review</option>

                        <option>Investigated</option>

                    </select>

                    <button
                        class="secondary"
                        onclick="updateStatus(${alertItem.id})"
                    >
                        Update status
                    </button>

                </article>

            `).join('')

            : '<p>No alerts yet. The demo rule creates an alert when at least three reports with matching crop, region and symptom keywords are present.</p>';

    } catch (e) {

        $('alertList').textContent = e.message;

    }

}

window.updateStatus = async id => {

    try {

        await request('/api/alerts/' + id, {
            method: 'PATCH',
            headers: {
                'Content-Type': 'application/json'
            },
            body: JSON.stringify({
                status: $('status-' + id).value
            })
        });

        loadAlerts();

    } catch (e) {

        alert(e.message);

    }

};

function esc(value) {

    return String(value ?? '').replace(/[&<>"']/g, character => ({

        '&': '&amp;',

        '<': '&lt;',

        '>': '&gt;',

        '"': '&quot;',

        "'": '&#39;'

    }[character]));

}
loadReports();