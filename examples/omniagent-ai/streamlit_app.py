
import asyncio
import base64
import binascii
import html
import json
import os
import re
import sys
import tempfile
import time
import traceback
import uuid
from pathlib import Path

import streamlit as st

ROOT = Path(__file__).resolve().parent
SRC = ROOT / 'src'
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

st.set_page_config(page_title='OmniAgent AI | Workspace', page_icon='✦', layout='wide')

try:
    from dotenv import load_dotenv
    load_dotenv(ROOT / '.env')
except ImportError:
    pass

# Make top-level Streamlit secrets available to the existing backend.
try:
    for name, value in st.secrets.items():
        if isinstance(value, (str, int, float)) and not os.getenv(name):
            os.environ[name] = str(value)
except FileNotFoundError:
    pass

THEMES = {
    'Midnight': {'bg':'#080d1a','panel':'#111b30','text':'#f4f6ff','muted':'#a6b5d0','accent':'#8388ff','border':'#2a3854'},
    'Aurora': {'bg':'#071b20','panel':'#103035','text':'#effffb','muted':'#a6d0cd','accent':'#2dd4bf','border':'#2c565b'},
    'Royal': {'bg':'#160d24','panel':'#29193e','text':'#fff7ff','muted':'#d2bde2','accent':'#c084fc','border':'#503464'},
    'Light': {'bg':'#f5f7fc','panel':'#ffffff','text':'#18243a','muted':'#62738b','accent':'#5759e8','border':'#d9e1ed'},
}

LANGUAGES = {
    'English': {'title':'Intelligence that speaks your language.', 'task':'Describe your task', 'upload':'Upload your files', 'run':'Run OmniAgent', 'results':'Execution Results'},
    'हिन्दी': {'title':'आपकी भाषा में बुद्धिमान तकनीक।', 'task':'अपना कार्य लिखें', 'upload':'फ़ाइलें अपलोड करें', 'run':'ओम्नीएजेंट चलाएं', 'results':'कार्य परिणाम'},
    'मराठी': {'title':'तुमच्या भाषेत बुद्धिमान तंत्रज्ञान.', 'task':'तुमचे कार्य लिहा', 'upload':'फाइल्स अपलोड करा', 'run':'ओम्नीएजंट चालवा', 'results':'कार्याचे निकाल'},
    'தமிழ்': {'title':'உங்கள் மொழியில் நுண்ணறிவு.', 'task':'உங்கள் பணியை விவரிக்கவும்', 'upload':'கோப்புகளைப் பதிவேற்றவும்', 'run':'OmniAgent இயக்கவும்', 'results':'செயல்பாட்டு முடிவுகள்'},
    'తెలుగు': {'title':'మీ భాషలో మేధస్సు.', 'task':'మీ పనిని వివరించండి', 'upload':'ఫైళ్లను అప్‌లోడ్ చేయండి', 'run':'OmniAgent అమలు చేయండి', 'results':'అమలు ఫలితాలు'},
    'বাংলা': {'title':'আপনার ভাষায় বুদ্ধিমত্তা।', 'task':'আপনার কাজ লিখুন', 'upload':'ফাইল আপলোড করুন', 'run':'OmniAgent চালান', 'results':'কাজের ফলাফল'},
    'ಕನ್ನಡ': {'title':'ನಿಮ್ಮ ಭಾಷೆಯಲ್ಲಿ ಬುದ್ಧಿವಂತಿಕೆ.', 'task':'ನಿಮ್ಮ ಕೆಲಸವನ್ನು ವಿವರಿಸಿ', 'upload':'ಫೈಲ್‌ಗಳನ್ನು ಅಪ್‌ಲೋಡ್ ಮಾಡಿ', 'run':'OmniAgent ಚಲಾಯಿಸಿ', 'results':'ಕಾರ್ಯದ ಫಲಿತಾಂಶಗಳು'},
}

for key, default in {
    'theme':'Midnight',
    'language':'English',
    'task_text':'',
    'last_run':None,
}.items():
    if key not in st.session_state:
        st.session_state[key] = default

def tr(key):
    return LANGUAGES[st.session_state.language].get(key, key)

def choose_example(value):
    st.session_state.task_text = value
    st.session_state.last_run = None

def inject_styles(theme_name):
    c = THEMES[theme_name]
    css = """
    <style>
    @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&display=swap');
    :root {
        --bg:__BG__; --panel:__PANEL__; --text:__TEXT__;
        --muted:__MUTED__; --accent:__ACCENT__; --border:__BORDER__;
    }
    html,body,.stApp {font-family:Inter,sans-serif;}
    .stApp {background:var(--bg);color:var(--text);}
    [data-testid="stSidebar"] {background:var(--panel);border-right:1px solid var(--border);}
    [data-testid="stSidebar"] * {color:var(--text);}
    [data-testid="stHeader"] {background:transparent;}
    .block-container {max-width:1360px;padding-top:2rem;padding-bottom:4rem;}
    h1,h2,h3,p,label {color:var(--text);}
    .hero {
        padding:40px 36px;border-radius:24px;
        background:linear-gradient(120deg,var(--panel),var(--bg));
        border:1px solid var(--border);margin-bottom:22px;
        animation:rise .5s ease-out both;
    }
    .eyebrow {font-size:12px;letter-spacing:2px;font-weight:800;color:var(--accent);}
    .hero-title {font-size:clamp(28px,4vw,45px);font-weight:800;letter-spacing:-1.3px;margin:12px 0;}
    .hero-subtitle {color:var(--muted);font-size:15px;line-height:1.7;}
    .feature-card {
        border:1px solid var(--border);background:var(--panel);
        border-radius:18px;padding:20px;min-height:110px;
        transition:transform .2s,border-color .2s;
    }
    .feature-card:hover {transform:translateY(-3px);border-color:var(--accent);}
    .feature-title {font-size:15px;font-weight:800;margin:10px 0 5px;}
    .feature-subtitle {font-size:12px;color:var(--muted);}
    .brand {font-size:23px;font-weight:800;}
    .brand-subtitle {font-size:11px;color:var(--muted);letter-spacing:1px;}
    div[data-testid="stTextArea"] textarea {
        background:var(--panel)!important;color:var(--text)!important;
        border:1px solid var(--border)!important;border-radius:12px!important;
    }
    div[data-testid="stFileUploader"] section {
        background:var(--panel)!important;
        border:1px dashed var(--accent)!important;border-radius:14px!important;
    }
    div[data-testid="stButton"] button[kind="primary"] {
        background:var(--accent)!important;color:white!important;
        border:0!important;border-radius:12px!important;
        min-height:48px;font-weight:800!important;
    }
    div[data-testid="stMetric"] {
        background:var(--panel);border:1px solid var(--border);
        padding:14px;border-radius:14px;
    }
    .output-box {
        border:1px solid var(--border);border-radius:14px;
        background:var(--panel);padding:18px;margin:12px 0;
    }
    .output-label {font-size:13px;font-weight:800;color:var(--accent);margin-bottom:8px;}
    .output-text {font-size:16px;color:var(--text);white-space:pre-wrap;overflow-wrap:anywhere;}
    @keyframes rise {
        from {opacity:0;transform:translateY(12px);}
        to {opacity:1;transform:translateY(0);}
    }
    @media (prefers-reduced-motion:reduce) {
        *,*::before,*::after {animation:none!important;transition:none!important;}
    }
    </style>
    """
    for k,v in {
        '__BG__':c['bg'], '__PANEL__':c['panel'], '__TEXT__':c['text'],
        '__MUTED__':c['muted'], '__ACCENT__':c['accent'], '__BORDER__':c['border'],
    }.items():
        css = css.replace(k,v)
    st.markdown(css, unsafe_allow_html=True)

def render_html(value):
    st.markdown(
        ' '.join(line.strip() for line in value.splitlines() if line.strip()),
        unsafe_allow_html=True,
    )

MAX_FILES = 3
MAX_FILE_BYTES = 15 * 1024 * 1024
ALLOWED = {'.png','.jpg','.jpeg','.wav'}

def save_uploads(files, directory):
    if len(files) > MAX_FILES:
        raise ValueError(f'Upload no more than {MAX_FILES} files.')
    saved = []
    used = set()
    for file in files:
        if file.size > MAX_FILE_BYTES:
            raise ValueError(f'{file.name} exceeds 15 MB.')
        original = Path(file.name.replace('\\','/')).name
        ext = Path(original).suffix.lower()
        if ext not in ALLOWED:
            raise ValueError(f'Unsupported extension: {ext}')
        stem = re.sub(r'[^A-Za-z0-9_-]','_',Path(original).stem)[:55] or 'upload'
        filename = f'{stem}{ext}'
        if filename in used:
            filename = f'{stem}_{uuid.uuid4().hex[:8]}{ext}'
        used.add(filename)
        (directory / filename).write_bytes(file.getvalue())
        saved.append(filename)
    return saved

async def execute_agent(task, input_dir, output_dir, steps, calls):
    from bodhan_agent.agent import OmniAgent
    agent = OmniAgent(input_dir=input_dir, output_dir=output_dir)
    return await agent.run(
        task=task,
        max_steps=steps,
        max_tool_calls=calls,
        max_attempts=3,
    )

def resolve_report_path(value, workspace):
    if not value:
        return None
    path = Path(str(value))
    if not path.is_absolute():
        path = ROOT / path
    path = path.resolve()
    allowed = [workspace.resolve(), ROOT.resolve()]
    if not any(path.is_relative_to(base) for base in allowed):
        return None
    if not path.is_file() or path.stat().st_size > 5 * 1024 * 1024:
        return None
    return path

def run_workflow(task, files, max_steps, max_calls):
    with tempfile.TemporaryDirectory(prefix='omniagent_web_') as temp:
        workspace = Path(temp)
        input_dir = workspace / 'inputs'
        output_dir = workspace / 'outputs'
        input_dir.mkdir()
        output_dir.mkdir()
        filenames = save_uploads(files, input_dir)
        full_task = (
            f'{task.strip()}\n\n'
            f'Uploaded input filenames: {", ".join(filenames)}\n'
            'Use these uploaded files for file-based operations.'
        )
        start = time.perf_counter()
        result = asyncio.run(
            execute_agent(full_task, input_dir, output_dir, max_steps, max_calls)
        )
        duration = time.perf_counter() - start
        if not isinstance(result, dict):
            raise TypeError(f'Agent returned {type(result).__name__}, expected dict.')

        reports = {}
        for key in ('report_path','result_path','trace_path'):
            path = resolve_report_path(result.get(key), workspace)
            if path:
                reports[key] = {'name':path.name,'content':path.read_bytes()}

        # IMPORTANT: The backend's canonical JSON report contains "steps".
        # If agent.run returns only report paths, read report.json too.
        report_candidates = []
        report_path = resolve_report_path(result.get('report_path'), workspace)
        if report_path:
            report_candidates.append(report_path)
        report_candidates.extend([
            output_dir / 'report.json',
            output_dir / 'result.json',
        ])
        for candidate in report_candidates:
            if candidate.is_file() and candidate.suffix.lower() == '.json':
                try:
                    parsed = json.loads(candidate.read_text(encoding='utf-8'))
                    if isinstance(parsed,dict) and 'steps' in parsed:
                        result = {**result, **parsed}
                        if 'report_path' not in reports:
                            reports['report_path'] = {
                                'name':candidate.name,
                                'content':candidate.read_bytes(),
                            }
                        break
                except (ValueError, OSError):
                    continue

        return {
            'result':result,
            'reports':reports,
            'filenames':filenames,
            'duration':duration,
        }

def parse_json(value):
    if isinstance(value,str) and value.strip().startswith(('{','[')):
        try:
            return json.loads(value)
        except ValueError:
            pass
    return value

def readable(value, depth=0):
    if depth > 8 or value is None:
        return ''
    value = parse_json(value)
    if isinstance(value,str):
        return value
    if isinstance(value,list):
        return '\n'.join(filter(None,(readable(x,depth+1) for x in value)))
    if isinstance(value,dict):
        for key in (
            'extracted_text','translated_text','transcription','transcript',
            'recognized_text','text','output','result','response','content'
        ):
            if key in value and value[key] is not None:
                text = readable(value[key],depth+1)
                if text:
                    return text
        return json.dumps(value,ensure_ascii=False,indent=2,default=str)
    return str(value)

def get_steps(result):
    steps = result.get('steps')
    if isinstance(steps,list):
        return steps
    history = result.get('history')
    if isinstance(history,list):
        return history
    return []

def get_output(step):
    if not isinstance(step,dict):
        return step
    for key in ('result','output','response','tool_result'):
        if key in step:
            return step[key]
    return step

def audio_from_result(value, depth=0):
    if depth > 8:
        return None
    value = parse_json(value)
    if isinstance(value,list):
        for item in value:
            audio = audio_from_result(item,depth+1)
            if audio:
                return audio
    if isinstance(value,dict):
        for key in ('audio_base64','audio_data','audio_content'):
            payload = value.get(key)
            if isinstance(payload,str):
                try:
                    decoded = base64.b64decode(payload,validate=True)
                    if 0 < len(decoded) <= 10*1024*1024:
                        return decoded
                except (ValueError,binascii.Error):
                    pass
        for key in ('result','output','response','content','data'):
            if key in value:
                audio = audio_from_result(value[key],depth+1)
                if audio:
                    return audio
    return None

def display_step(step, index):
    if not isinstance(step,dict):
        st.write(step)
        return
    tool = str(step.get('tool','Unknown tool'))
    result = get_output(step)
    output_text = readable(result)
    tool_lower = tool.lower()
    if 'extract' in tool_lower or 'ocr' in tool_lower:
        label = '🔎 Extracted Text'
    elif 'translat' in tool_lower:
        label = '🌐 Translated Text'
    elif 'transcrib' in tool_lower:
        label = '🎙️ Transcription'
    elif 'speech' in tool_lower or 'tts' in tool_lower or 'speak' in tool_lower:
        label = '🔊 Generated Speech'
    else:
        label = '⚙️ Tool Output'

    st.markdown(f'#### Step {index} · `{tool}`')
    if isinstance(result,dict) and result.get('status') == 'error':
        st.error(output_text or 'Tool reported an error.')
    elif output_text:
        render_html(
            '<div class="output-box">'
            f'<div class="output-label">{html.escape(label)}</div>'
            f'<div class="output-text">{html.escape(output_text)}</div>'
            '</div>'
        )
    else:
        st.info('This tool did not return readable text.')

    audio = audio_from_result(result)
    if audio:
        st.audio(audio)
    with st.expander('View raw tool response'):
        st.json(step)

with st.sidebar:
    render_html('<div class="brand">✦ OmniAgent AI</div><div class="brand-subtitle">INTELLIGENT AGENT PLATFORM</div>')
    st.selectbox('🌐 Interface Language',list(LANGUAGES),key='language')
    st.selectbox('🎨 Appearance',list(THEMES),key='theme')
    st.divider()
    st.subheader('⚙️ Execution Settings')
    max_steps = st.slider('Planning steps',1,12,4)
    max_calls = st.slider('Tool-call budget',1,6,3)
    st.caption('API usage may incur costs.')
    st.divider()
    st.caption('Gemini 2.5 Flash · Bodhan AI · MCP')
    st.caption('Do not upload sensitive files to a public demonstration app.')

inject_styles(st.session_state.theme)

render_html(
    '<div class="hero">'
    '<div class="eyebrow">✦ MULTILINGUAL AGENTIC WORKSPACE</div>'
    f'<div class="hero-title">{html.escape(tr("title"))}<br>'
    '<span style="color:var(--accent)">OmniAgent AI</span></div>'
    '<div class="hero-subtitle">Extract text, translate Indian languages, '
    'transcribe speech and orchestrate intelligent workflows with Gemini and Bodhan AI.</div>'
    '</div>'
)

columns = st.columns(3)
for column, icon, title, detail in zip(
    columns,
    ['◈','◎','⌘'],
    ['AI Planner','Multilingual Engine','Tool Protocol'],
    ['Gemini 2.5 Flash','Bodhan AI','Model Context Protocol'],
):
    with column:
        render_html(
            f'<div class="feature-card"><div>{icon}</div>'
            f'<div class="feature-title">{title}</div>'
            f'<div class="feature-subtitle">{detail}</div></div>'
        )

st.write('')
left,right = st.columns([1.6,1],gap='large')
with left:
    st.markdown(f'### {tr("task")}')
    task = st.text_area(
        tr('task'),
        key='task_text',
        height=180,
        placeholder='Extract Hindi text from my uploaded image and translate it into English.',
        label_visibility='collapsed',
    )
    st.caption('Quick-start workflows')
    a,b,c = st.columns(3)
    with a:
        st.button('🔎 OCR',use_container_width=True,on_click=choose_example,args=('Extract text from my uploaded image.',))
    with b:
        st.button('🌐 Translate',use_container_width=True,on_click=choose_example,args=('Extract text from my uploaded image and translate it into English.',))
    with c:
        st.button('🎙️ Transcribe',use_container_width=True,on_click=choose_example,args=('Transcribe speech from my uploaded WAV file.',))
with right:
    st.markdown(f'### {tr("upload")}')
    files = st.file_uploader(
        'PNG, JPG, JPEG or WAV',
        type=['png','jpg','jpeg','wav'],
        accept_multiple_files=True,
        help='Maximum 3 files, 15 MB each.',
    )
    if files:
        for file in files:
            st.caption(f'📎 {file.name} ({file.size/1024:.1f} KB)')
    st.info('Supported workflows: OCR, translation, speech transcription and speech generation.')

st.write('')
if st.button(f'✦ {tr("run")}',type='primary',use_container_width=True):
    if not task.strip():
        st.warning('Please describe a task.')
    elif not files:
        st.warning('Please upload at least one file.')
    else:
        with st.spinner('OmniAgent is executing your workflow...'):
            try:
                st.session_state.last_run = run_workflow(task,files,max_steps,max_calls)
            except Exception as exc:
                st.session_state.last_run = None
                traceback.print_exc()
                st.error('OmniAgent execution failed.')
                st.warning(f'Error type: {type(exc).__name__}')
                st.info('Read the complete traceback in your VS Code terminal. Redact credentials before sharing.')

execution = st.session_state.last_run
if execution:
    result = execution['result']
    reports = execution['reports']
    steps = get_steps(result)
    status = str(result.get('status','unknown'))
    st.divider()
    st.header(tr('results'))
    if status.lower() == 'success':
        st.success('Workflow completed successfully.')
    else:
        st.warning(f'Workflow status: {status}')

    metrics = st.columns(4)
    metrics[0].metric('Status',status.upper())
    metrics[1].metric('Completed Steps',result.get('completed_steps',len(steps)))
    metrics[2].metric('Tool Calls',result.get('budget',{}).get('tool_calls_used',len(steps)))
    metrics[3].metric('Duration',f'{execution["duration"]:.1f}s')

    output_tab,activity_tab,reports_tab = st.tabs(
        ['✨ Actual Outputs','⚙️ Tool Activity','📦 Reports']
    )
    with output_tab:
        if steps:
            # Display the last successful text result as the final output.
            final_text = ''
            for step in reversed(steps):
                value = get_output(step)
                if isinstance(value,dict) and value.get('status') == 'error':
                    continue
                text = readable(value)
                if text:
                    final_text = text
                    break
            if final_text:
                st.subheader('Final Agent Output')
                render_html(
                    '<div class="output-box">'
                    f'<div class="output-text">{html.escape(final_text)}</div>'
                    '</div>'
                )
            st.subheader('Step-by-Step Results')
            for index,step in enumerate(steps,1):
                display_step(step,index)
                st.divider()
        else:
            st.info('No steps were returned. Check the report and execution trace.')

    with activity_tab:
        if steps:
            for index,step in enumerate(steps,1):
                with st.expander(f'Step {index}: {step.get("tool","Unknown tool")}',expanded=(index==1)):
                    st.json(step)
        else:
            st.info('No tool activity was recorded.')
        with st.expander('Full agent result'):
            st.json(result)

    with reports_tab:
        labels = {
            'report_path':('JSON Report','application/json'),
            'result_path':('Markdown Summary','text/markdown'),
            'trace_path':('Execution Trace','application/jsonl'),
        }
        for key,(label,mime) in labels.items():
            report = reports.get(key)
            if report:
                st.download_button(
                    f'⬇ Download {label}',
                    data=report['content'],
                    file_name=report['name'],
                    mime=mime,
                    use_container_width=True,
                    key=f'download_{key}',
                )
        st.download_button(
            '⬇ Download Current Results (JSON)',
            data=json.dumps(result,ensure_ascii=False,indent=2,default=str),
            file_name='omniagent_results.json',
            mime='application/json',
            use_container_width=True,
        )

st.divider()
st.caption('OmniAgent AI · Gemini + Bodhan AI + MCP · Built with Python and Streamlit')
