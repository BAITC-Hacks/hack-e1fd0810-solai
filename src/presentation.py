"""Display-only theme, labels and formatting. Never mutates engine data."""

import re
from numbers import Number

import pandas as pd
import streamlit as st

from src.decision_intelligence import format_number
from src.product_translator import translate_product_name
from src.translations import TRANSLATIONS

PALETTE = {"CRITICAL": "#9D5551", "HIGH": "#AD7359", "MEDIUM": "#A1833E", "LOW": "#718268"}
TINTS = {"CRITICAL": "#F3E5E3", "HIGH": "#F5EAE3", "MEDIUM": "#F5F0DF", "LOW": "#E8EEE2"}
TEXT = {
    "overview": ("Overview", "Обзор", "Шолу"),
    "forecast": ("Forecast", "Прогноз", "Болжам"),
    "replenishment": ("Replenishment", "Пополнение", "Толықтыру"),
    "warehouse": ("Warehouse", "Склад", "Қойма"),
    "copilot": ("AI Copilot", "AI-помощник", "AI көмекші"),
    "approvals": ("Approvals", "Согласования", "Мақұлдау"),
    "subtitle": ("Demand intelligence and replenishment decision support.", "Анализ спроса и поддержка решений по закупкам.", "Сұранысты талдау және толықтыру шешімдерін қолдау."),
    "unavailable": ("Not available", "Нет данных", "Дерек жоқ"),
    "insufficient_history": ("Insufficient sales history for this SKU. No usable dated sales observations are available.", "Недостаточно истории продаж для этого SKU. Нет доступных наблюдений продаж с корректной датой.", "Бұл SKU бойынша сату тарихы жеткіліксіз. Дұрыс күні бар сату деректері жоқ."),
    "active": ("Active SKUs", "Анализируемые SKU", "Талданатын SKU"),
    "action": ("SKUs requiring action", "SKU требуют внимания", "Назар аударуды қажет ететін SKU"),
    "urgent": ("Critical / High urgency", "Критичная / высокая срочность", "Сындарлы / жоғары жеделдік"),
    "affected": ("Suppliers affected", "Поставщики с заказами", "Тапсырысы бар жеткізушілер"),
    "quality": ("Data quality", "Качество данных", "Дерек сапасы"),
    "priority": ("Priority Queue", "Очередь приоритетов", "Басымдық кезегі"),
    "priority_rule": ("Ordered by urgency, review required, shortest known on-hand coverage, positive order requirement, then SKU. Unknown coverage follows known coverage within each band. No AI score.", "Порядок: срочность, необходимость проверки, наименьшее известное покрытие запасом, наличие заказа, затем SKU. Неизвестное покрытие — после известного в каждой группе. Без скрытого AI-рейтинга.", "Реті: жеделдік, тексеру қажеттілігі, белгілі қордың ең қысқа мерзімі, тапсырыс қажеттілігі, содан кейін SKU. Белгісіз мерзім әр топта соңында. Жасырын AI ұпайы жоқ."),
    "signals": ("Key signals", "Ключевые сигналы", "Негізгі белгілер"),
    "no_signals": ("No supported change signals for this SKU.", "Подтверждённых сигналов изменений нет.", "Расталған өзгеріс белгілері жоқ."),
    "generate": ("Generate decision brief", "Сформировать обзор решений", "Шешім шолуын құру"),
    "analyze": ("Analyze SKU", "Проанализировать SKU", "SKU талдау"),
    "brief": ("Decision Brief", "Обзор решений", "Шешім шолуы"),
    "portfolio": ("Portfolio", "Портфель", "Портфель"),
    "immediate": ("Immediate attention", "Приоритетное внимание", "Шұғыл назар"),
    "supplier_impact": ("Supplier impact", "Влияние на поставщиков", "Жеткізушілерге әсері"),
    "data_risks": ("Data risks", "Риски данных", "Дерек тәуекелдері"),
    "next": ("Next action", "Следующее действие", "Келесі әрекет"),
    "recommendation": ("Recommendation", "Рекомендация", "Ұсыныс"),
    "risk": ("Risk", "Риск", "Тәуекел"),
    "strong_data": ("Strong data", "Достаточные данные", "Жеткілікті деректер"),
    "limited_data": ("Limited data", "Ограниченные данные", "Шектеулі деректер"),
    "review_required": ("Review required", "Требуется проверка", "Тексеру қажет"),
    "quality_rule": ("Evidence labels, not confidence probabilities. Review required: missing critical inputs or engine review flags. Limited: fewer than 6 baseline / 12 variability observations, unknown stockout metadata or unavailable seasonality. Strong: none of those limitations.", "Это оценка полноты данных, не вероятность. Проверка: нет ключевых входных данных или есть флаги проверки. Ограничено: менее 6 базовых / 12 наблюдений вариативности, нет данных о дефиците или сезонности. Иначе данные достаточны.", "Бұл ықтималдық емес, деректер бағасы. Тексеру: негізгі деректер жоқ немесе тексеру белгісі бар. Шектеулі: 6 базалық / 12 өзгермелілік бақылаудан аз, тапшылық не маусымдық дерек жоқ. Өзге жағдайда деректер жеткілікті."),
    "deterministic": ("Deterministic intelligence", "Детерминированный анализ", "Детерминдік талдау"),
    "copilot_note": ("Uses calculated evidence only. No external model is used in deterministic mode; no order is sent. Briefs cover all filtered results, not only the visible page.", "Использует только рассчитанные факты. В детерминированном режиме внешняя модель не используется, заказы не отправляются. Обзор охватывает все результаты фильтра.", "Тек есептелген деректер қолданылады. Детерминдік режим сыртқы модельді қолданбайды, тапсырыс жібермейді. Шолу барлық сүзілген нәтижені қамтиды."),
    "stale": ("Generate a fresh brief for this selection and calculation. Previous results are not reused after context changes.", "Сформируйте новый обзор для текущего выбора и расчёта. Устаревшие результаты не показываются.", "Ағымдағы таңдау мен есеп үшін жаңа шолу құрыңыз. Ескірген нәтижелер көрсетілмейді."),
    "all": ("All suppliers", "Все поставщики", "Барлық жеткізушілер"),
    "search": ("Search SKU or product", "Поиск SKU или товара", "SKU не тауар іздеу"),
    "urgency_filter": ("Urgency", "Срочность", "Жеделдік"),
    "status_filter": ("Status", "Статус", "Мәртебе"),
    "settings": ("Calculation settings", "Настройки расчёта", "Есеп баптаулары"),
    "scope": ("{total} filtered SKUs · {visible} shown · page {page}/{pages}. KPIs and brief use all filtered results.", "SKU после фильтра: {total} · показано: {visible} · страница {page}/{pages}. Метрики и обзор учитывают все результаты.", "Сүзілген SKU: {total} · көрсетілгені: {visible} · бет {page}/{pages}. Метрикалар мен шолу барлық нәтижені қамтиды."),
    "empty": ("No SKUs match these filters. Clear a filter to continue.", "Нет SKU по выбранным фильтрам. Измените фильтр.", "Сүзгіге сәйкес SKU жоқ. Сүзгіні өзгертіңіз."),
    "unknown_orders": ("{n} order quantities are unavailable and excluded from the unit total. Unknown is not zero.", "Неизвестных количеств заказа: {n}; они исключены из суммы. Неизвестно не означает ноль.", "{n} тапсырыс саны белгісіз және жиынтыққа кірмейді. Белгісіз — нөл емес."),
    "full_table": ("All calculation fields", "Все поля расчёта", "Барлық есеп өрістері"),
    "why": ("Why this recommendation?", "Почему такая рекомендация?", "Бұл ұсыныстың себебі қандай?"),
    "source": ("Source data", "Исходные данные", "Бастапқы деректер"),
    "derived": ("Derived values", "Расчётные значения", "Есептелген мәндер"),
    "provenance": ("Data provenance and calculation details", "Источники данных и детали расчёта", "Дерек көздері және есеп мәліметтері"),
    "source_note": ("Sales, stock and transit originate in the selected source. Planning lead time is a manager assumption when labelled as such. Forecast, safety stock, target, order and urgency are calculated, not Excel facts.", "Продажи, запасы и поставки в пути взяты из выбранного источника. Плановый срок — допущение менеджера, если так указано. Прогноз, страховой и целевой запас, заказ и срочность рассчитаны, а не взяты из Excel.", "Сату, қор және жолдағы тауар таңдалған көзден алынады. Белгіленген жоспарлы мерзім — менеджер жорамалы. Болжам, сақтандыру және мақсатты қор, тапсырыс пен жеделдік есептеледі."),
    "calculated": ("Calculated recommendation", "Расчётная рекомендация", "Есептелген ұсыныс"),
    "reviewed": ("Manager review", "Проверка менеджером", "Менеджер тексеруі"),
    "approval": ("Explicit approval", "Явное согласование", "Нақты мақұлдау"),
    "workflow": ("Calculated recommendation / Manager review / Optional adjustment / Explicit approval", "Расчётная рекомендация / Проверка / Корректировка / Явное согласование", "Есептелген ұсыныс / Тексеру / Өзгерту / Нақты мақұлдау"),
    "session_only": ("Decisions persist in this browser session only. Changing a quantity or calculation resets approval. No supplier dispatch is connected.", "Решения хранятся только в текущей сессии. Изменение количества или расчёта отменяет согласование. Отправка поставщику не подключена.", "Шешімдер тек осы сессияда сақталады. Сан не есеп өзгерсе, мақұлдау жойылады. Жеткізушіге жіберу қосылмаған."),
    "history": ("Decision history for selected SKU", "История решений по SKU", "SKU шешімдер тарихы"),
    "coverage": ("On-hand coverage / supplier lead time", "Покрытие запасом / срок поставки", "Қор мерзімі / жеткізу мерзімі"),
    "urgency_distribution": ("Urgency across filtered SKUs", "Срочность отфильтрованных SKU", "Сүзілген SKU жеделдігі"),
    "no_3d": ("No 3D warehouse component is installed. This inventory view uses real calculated quantities; no storage locations are invented.", "3D-компонент склада не установлен. Показаны реальные расчётные количества; места хранения не выдумываются.", "Қойманың 3D компоненті орнатылмаған. Нақты есептелген сандар көрсетіледі, сақтау орындары ойдан шығарылмайды."),
    "confirm_missing_inputs": ("Confirm missing forecast, stock, transit or lead-time inputs before setting or approving a quantity.", "Подтвердите недостающие прогноз, запас, поставки в пути или срок до ввода и согласования количества.", "Санды енгізіп, мақұлдамас бұрын жетіспейтін болжам, қор, жолдағы тауар немесе мерзімді растаңыз."),
    "review_urgent": ("Review critical/high urgency SKUs and verify inbound arrival timing first.", "Сначала проверьте критичные и срочные SKU и сроки поступления товара.", "Алдымен жедел SKU мен тауардың келу мерзімдерін тексеріңіз."),
    "review_then_approve": ("Review the evidence and supplier constraints, adjust only if needed, then explicitly approve the reviewed proposal.", "Проверьте расчёт и ограничения поставщика, при необходимости скорректируйте, затем явно согласуйте предложение.", "Есеп пен жеткізуші шектеулерін тексеріп, қажет болса өзгертіңіз, содан кейін ұсынысты мақұлдаңыз."),
    "review_data": ("Resolve the reported data issues before accepting this recommendation.", "Устраните отмеченные проблемы данных перед принятием рекомендации.", "Ұсынысты қабылдамас бұрын көрсетілген дерек мәселелерін шешіңіз."),
    "monitor": ("No positive calculated order is indicated; monitor inventory and demand at the next review.", "Положительный расчётный заказ не требуется; следите за запасом и спросом при следующем пересмотре.", "Оң есептелген тапсырыс қажет емес; келесі тексеруде қор мен сұранысты бақылаңыз."),
    "brief_portfolio": ("{skus} SKUs analyzed; {orders} require replenishment; {urgent} have critical/high urgency. Known recommended units: {units}. Unavailable quantities: {unknown}.", "Проанализировано {skus} SKU; пополнение нужно для {orders}; критичных/срочных — {urgent}. Известный объём заказа: {units}. Неизвестных количеств: {unknown}.", "{skus} SKU талданды; {orders} толықтыру қажет; {urgent} жедел. Белгілі ұсынылған сан: {units}. Белгісіз сандар: {unknown}."),
    "brief_supplier": ("{supplier}: {units} recommended units across {skus} SKUs.", "{supplier}: рекомендовано {units} единиц по {skus} SKU.", "{supplier}: {skus} SKU бойынша {units} бірлік ұсынылды."),
    "no_attention": ("No urgent or positive-order items in this filtered selection.", "В выборке нет срочных позиций или положительных заказов.", "Таңдауда жедел позиция не оң тапсырыс жоқ."),
    "no_positive": ("No positive calculated supplier proposals in this selection.", "В выборке нет положительных расчётных предложений поставщикам.", "Таңдауда жеткізушіге оң есептелген ұсыныс жоқ."),
    "no_risks": ("No issues detected by the stated data-quality checks.", "По указанным проверкам проблемы данных не обнаружены.", "Көрсетілген тексерулер бойынша дерек мәселесі табылмады."),
    "growth": ("Sustained growth increased the forecast by {percent}% after seasonality.", "Устойчивый рост увеличил прогноз на {percent}% после сезонной корректировки.", "Тұрақты өсім маусымдық түзетуден кейін болжамды {percent}% арттырды."),
    "spikes": ("Excluded {monthly} recent monthly spikes and {documents} reconciled document spikes; audit retained.", "Исключено месячных пиков: {monthly}, сверенных пиков документов: {documents}; аудит сохранён.", "{monthly} айлық және {documents} салыстырылған құжат ауытқуы шығарылды; аудит сақталды."),
    "stockout": ("{periods} historical stockout periods; estimated lost demand {lost} units, not an extra order quantity.", "Исторических периодов дефицита: {periods}; оценка потерянного спроса {lost}, это не дополнительный заказ.", "{periods} тарихи тапшылық кезеңі; жоғалған сұраныс бағасы {lost}, бұл қосымша тапсырыс емес."),
    "lead_exposure": ("On-hand stock {stock} is below calculated lead-time demand {lead}; check transit timing.", "Текущий запас {stock} ниже спроса за срок поставки {lead}; проверьте даты поступления.", "Ағымдағы қор {stock} жеткізу мерзіміндегі сұраныстан {lead} төмен; келу күнін тексеріңіз."),
    "above_target": ("Inventory position {position} exceeds twice the calculated target {target}.", "Позиция запаса {position} превышает двойной расчётный целевой запас {target}.", "Қор позициясы {position} есептелген мақсатты қордың {target} екі есесінен көп."),
    "constraint": ("Supplier constraints increased the raw requirement from {raw} to {final} units.", "Ограничения поставщика увеличили исходную потребность с {raw} до {final} единиц.", "Жеткізуші шектеулері бастапқы қажеттілікті {raw} санынан {final} бірлікке арттырды."),
    "history_signal": ("Only {observations} clean variability observations; the engine flags limited history.", "Чистых наблюдений вариативности: {observations}; движок отмечает ограниченную историю.", "Таза өзгермелілік бақылауы: {observations}; қозғалтқыш тарих шектеуін белгілейді."),
    "sales_decline": ("Clean observed sales declined {percent}% between three-month medians, with at least four decreases. Not seasonally adjusted; the forecast is unchanged by this signal.", "Чистые продажи снизились на {percent}% между трёхмесячными медианами при минимум четырёх снижениях. Без сезонной корректировки; сигнал не меняет прогноз.", "Таза сату үш айлық медианалар арасында {percent}% төмендеді, кемінде төрт төмендеу бар. Маусымдық түзетусіз; белгі болжамды өзгертпейді."),
}


def text(key, language=None):
    language = language or st.session_state.get("language", "en")
    if key in TEXT:
        return TEXT[key][{"en": 0, "ru": 1, "kz": 2}.get(language, 0)]
    translated = TRANSLATIONS.get(language, TRANSLATIONS["en"])
    fallback = key.removeprefix("col_").replace("_", " ").capitalize()
    return translated.get(key, TRANSLATIONS["en"].get(key, fallback))


def clean_text(value):
    return re.sub(r"(?<!\w)(?:nan|None|<NA>|NaT)(?!\w)", text("unavailable"), str(value))


def show_table(frame, *, height=None, urgency=False):
    """Format a display-only copy; calculations and explicit CSV exports stay typed.

    Streamlit's numeric grid overrides Styler's null formatting. Only columns
    with nulls become display strings, so unknown table values read '—'.
    """
    shown = frame.copy()
    for column in shown.select_dtypes(include=["object", "string"]):
        shown[column] = shown[column].map(lambda v: pd.NA if isinstance(v, str)
            and v.strip() in {"", "None", "nan", "NaN", "<NA>", "NaT", "Not available", "Нет данных", "Дерек жоқ"} else v)
    if "product_name" in shown:
        names = {name: translate_product_name(name, st.session_state.get("language", "en"))
                 for name in shown.product_name.dropna().unique()}
        shown["product_name"] = shown.product_name.map(lambda name: names.get(name, name))
    for column in shown:
        if shown[column].isna().any():
            def display_value(value):
                if isinstance(value, (list, dict, tuple)):
                    return str(value)
                if pd.isna(value):
                    return "—"
                return format_number(value) if isinstance(value, Number) and not isinstance(value, bool) else str(value)
            shown[column] = shown[column].map(display_value)
    style = shown.style.format(precision=2, na_rep="—")
    if urgency and "urgency" in shown:
        style = style.map(lambda v: f"background-color:{TINTS.get(v, '#FFFFFF')};color:#20241E;font-weight:600", subset=["urgency"])
    config = {c: st.column_config.Column(label=text(f"col_{c}") if f"col_{c}" in TRANSLATIONS["en"] else c.replace("_", " ").title()) for c in shown}
    return st.dataframe(style, hide_index=True, width="stretch", height=height or "auto", column_config=config)


def metric(label, value):
    st.metric(label, format_number(value, text("unavailable")))


def style_chart(fig, height=330):
    fig.update_layout(template="plotly_white", paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
                      font=dict(family="Arial, sans-serif", size=12, color="#20241E"), height=height,
                      margin=dict(l=15, r=20, t=45, b=25), colorway=["#718268", "#B3BEA6", "#A1833E", "#AD7359"],
                      legend=dict(orientation="h", y=-.22, x=0), title_font_size=16)
    fig.update_xaxes(showgrid=False, zeroline=False)
    fig.update_yaxes(gridcolor="#E5E8E0", zeroline=False)
    return fig


def apply_theme():
    # Static CSS only. Product names, supplier text and model output never enter HTML.
    st.markdown("""<style>
      .stApp {background:#F6F7F2;color:#20241E;}
      [data-testid="stHeader"] {background:#F6F7F2;}
      [data-testid="stSidebar"] {background:#EEF1E8;border-right:1px solid #DDE1D8;}
      .block-container {padding-top:4.25rem;padding-bottom:2rem;max-width:1500px;}
      h1 {font-size:1.6rem!important;font-weight:650!important;letter-spacing:.08em;margin-bottom:0;}
      .solai-brand {display:flex;align-items:baseline;gap:20px;color:#20241E;}
      .solai-brand h1 {font-size:1.6rem!important;letter-spacing:.12em!important;font-weight:650!important;padding:0!important;margin:0!important;}
      .solai-brand span {font-size:1rem;letter-spacing:.015em;}
      h2 {font-size:1.25rem!important;font-weight:600!important;}
      h3 {font-size:1.05rem!important;font-weight:600!important;}
      p,li {line-height:1.6;}
      [data-testid="stCaptionContainer"],[data-testid="stCaptionContainer"] p {color:#596052!important;}
      [data-testid="stMetric"] {background:#FFF;border:1px solid #DDE1D8;border-radius:6px;padding:12px;min-height:115px;}
      [data-testid="stMetricValue"],[data-testid="stMetricValue"]>div {font-size:1.3rem;font-weight:600;white-space:normal!important;overflow:visible!important;text-overflow:clip!important;}
      [data-testid="stMetricLabel"],[data-testid="stMetricLabel"] p {font-size:.78rem;color:#596052;white-space:normal!important;line-height:1.35;}
      [data-testid="stVerticalBlockBorderWrapper"]>div {border-color:#DDE1D8!important;border-radius:6px!important;}
      [data-baseweb="tab-list"] {gap:1.5rem;border-bottom:1px solid #DDE1D8;margin:0 0 1.2rem;}
      [data-baseweb="tab"] {padding:12px 0;background:transparent;font-weight:500;color:#596052;}
      [aria-selected="true"][data-baseweb="tab"] {color:#39472D;font-weight:650;}
      [data-baseweb="tab-highlight"] {background:#566347;}
      .stButton>button,.stDownloadButton>button {border-radius:5px;border:1px solid #CBD2C1;font-weight:500;box-shadow:none;}
      .stButton>button[kind="primary"] {background:#566347;color:#FFF;border-color:#566347;}
      .stButton>button:focus-visible {outline:3px solid #7F8F6A;outline-offset:3px;}
      [data-testid="stDataFrame"] {border:1px solid #DDE1D8;border-radius:5px;}
      [data-testid="stExpander"] {background:#FFF;border-color:#DDE1D8;border-radius:5px;}
      [data-testid="stAlert"] {border-radius:5px;box-shadow:none;}
      @media(max-width:700px) {.block-container{padding-left:1rem;padding-right:1rem;}[data-baseweb="tab-list"]{gap:.8rem;}}
    </style>""", unsafe_allow_html=True)
