/* ushi - UI translation layer.
 *
 * Self-contained: app.js renders English; this file translates the rendered DOM (text nodes and
 * a few attributes) from exact English strings, and keeps translating whatever app.js renders
 * later (MutationObserver). User data (transcript, chat, notes, file / project / window names,
 * logs, the Beautified document) is never touched: see DATA below.
 *
 * Adding a language: add I18N.<code> = { strings: { "English": "…" }, patterns: [[/^…$/, "…$1" | fn]] }
 * and a button in the toggle. Keys are matched after trimming and collapsing whitespace.
 */
(function () {
  'use strict';

  // ── Russian helpers ──────────────────────────────────────────────────────
  // Russian plural form for an integer: 1 строка, 2 строки, 5 строк.
  const pl = (n, one, few, many) => {
    const a = Math.abs(parseInt(n, 10)) % 100, b = a % 10;
    if (a > 10 && a < 20) return many;
    if (b === 1) return one;
    if (b > 1 && b < 5) return few;
    return many;
  };
  const cnt = (n, one, few, many) => `${n} ${pl(n, one, few, many)}`;
  // Units inside numbers we pass through: "1.5 s" → "1.5 с", "1.2 GB" → "1.2 ГБ".
  const units = s => String(s)
    .replace(/(\d) s\b/g, '$1 с').replace(/\bGB\b/g, 'ГБ').replace(/\bMB\b/g, 'МБ').replace(/\bKB\b/g, 'КБ');
  const dur = s => String(s).replace(/^(\d+)m (\d+)s$/, '$1 мин $2 с').replace(/^(\d+)s$/, '$1 с');

  const I18N = {
    ru: {
      strings: {
        // ── Top bar, tabs, static page ──
        'Project': 'Проект',
        'Session': 'Сессия',
        'What gets captured when you take a screenshot': 'Что попадает в скриншот',
        'Capture': 'Захват',
        'Full screen': 'Весь экран',
        'Area': 'Область',
        'Window': 'Окно',
        'Toggle theme': 'Сменить тему',
        'Settings: AI provider, project, recognizer': 'Настройки: ИИ-провайдер, проект, распознаватель',
        'Settings': 'Настройки',
        'Transcript': 'Транскрипт',
        'Beautified': 'Документ',
        '↓ Jump to live': '↓ К текущему моменту',
        'Beautified transcript': 'Оформленный транскрипт',
        'Screenshots': 'Скриншоты',
        'Automatic screen frames': 'Автоматические кадры экрана',
        'Frames': 'Кадры',
        'Ask': 'Спросить',
        'Notes': 'Заметки',
        'Take a screenshot now': 'Сделать скриншот сейчас',
        'Snap': 'Снимок',
        'Start a new conversation': 'Начать новый разговор',
        'New chat': 'Новый чат',
        'Ask about the transcript…': 'Спросите о транскрипте…',
        'Send (Enter)': 'Отправить (Enter)',
        'Recording keeps running while you chat.': 'Запись продолжается, пока вы общаетесь.',
        'Ask anything about this transcript.': 'Спросите что угодно об этом транскрипте.',
        'Sent with every': 'Отправляется с каждым запросом',
        'and': 'и',
        "request in this project: who's who, the client, what was wrong last time.": 'в этом проекте: кто есть кто, клиент, что было не так в прошлый раз.',
        'Select text in the transcript': 'Выделите текст в транскрипте,',
        'to add a correction or say who said it. Corrections also guide live recognition.': 'чтобы добавить исправление или указать, кто это сказал. Исправления также подсказывают распознаванию во время записи.',
        'External transcript': 'Внешний транскрипт',
        'Teams transcript of the same meeting (.vtt, .docx, .txt)': 'Транскрипт Teams той же встречи (.vtt, .docx, .txt)',
        'Upload…': 'Загрузить…',
        // Notes placeholder: the "## Corrections" / "## Who said it" headings are parsed by the server, keep them.
        'e.g.\nWe are the ... team. Client: ... \nSpeaker at 11:06 is .....\nDon\'t call the system “XXXXX” - it\'s XXXXXY.\n\n## Corrections (speech-to-text: heard → meant)\n- "hybrids" → "Hybris"\n\n## Who said it (quote <-- who said it)\n- "Let\'s come back to pricing later" <-- Jane Doe':
          'например:\nМы - команда ... Клиент: ...\nСпикер в 11:06 - это .....\nНе называть систему «XXXXX» - правильно XXXXXY.\n\n## Corrections (распознано → должно быть)\n- "гибриды" → "Hybris"\n\n## Who said it (цитата <-- кто сказал)\n- "Вернёмся к ценам позже" <-- Иван Петров',
        'Connecting…': 'Подключение…',
        'Connected': 'Подключено',
        'Disconnected from server - retrying…': 'Нет связи с сервером - переподключение…',
        'screenshot': 'скриншот',
        'selected text': 'выделенный текст',
        'Recognizer console': 'Консоль распознавателя',
        'Close': 'Закрыть',

        // ── Projects & sessions ──
        'Projects': 'Проекты',
        'New project…': 'Новый проект…',
        'Choose…': 'Выберите…',
        'Recording continues in the background.': 'Запись продолжается в фоне.',
        'New project': 'Новый проект',
        'Each project keeps its own transcripts, screenshots and documents in': 'Каждый проект хранит свои транскрипты, скриншоты и документы в',
        'Name': 'Название',
        'e.g. acme-discovery': 'например, acme-discovery',
        'Cancel': 'Отмена',
        'Create project': 'Создать проект',
        'Sessions': 'Сессии',
        'No recordings yet': 'Записей пока нет',
        'Starting…': 'Запуск…',
        'No sessions': 'Нет сессий',
        'New session': 'Новая сессия',
        'Next recording starts a fresh transcript': 'Следующая запись начнёт новый транскрипт',
        'FINAL': 'ФИНАЛ',
        'Recording now': 'Идёт запись',
        'speakers': 'спикеры',
        'Live': 'Запись',

        // ── Recorder control strip ──
        'Transcribe file': 'Распознать файл',
        'Start recording': 'Начать запись',
        'times are positions in the file': 'время - позиция в файле',
        'Appends to': 'Дописывается в',
        'new session instead': 'лучше новая сессия',
        'Starts a new session': 'Начнётся новая сессия',
        'use headphones': 'используйте наушники',
        'Engine': 'Движок',
        'Speech recognition model': 'Модель распознавания речи',
        'Language': 'Язык',
        'Auto': 'Авто',
        'Speakers': 'Спикеры',
        'Detect': 'Определить',
        'Off': 'Выкл',
        'Auto-stop': 'Автостоп',
        'min': 'мин',
        's': 'с',
        'Save audio': 'Сохранять аудио',
        'Capture the screen every second and keep the frames that show something new': 'Снимать экран каждую секунду и сохранять кадры, на которых появилось что-то новое',
        'Auto frames': 'Автокадры',
        'Recognizer error': 'Ошибка распознавателя',
        'Show console': 'Показать консоль',
        'Dismiss': 'Скрыть',
        'Starting recording…': 'Запуск записи…',
        'Preparing the recognizer': 'Подготовка распознавателя',
        'Launching…': 'Запуск…',
        'the first start can take a minute': 'первый запуск может занять минуту',
        'Stopping…': 'Остановка…',
        'Finishing the last phrase': 'Дописываю последнюю фразу',
        'Finalizing session': 'Завершение сессии',
        'Transcribing the remaining speech': 'Распознаю оставшуюся речь',
        'then speakers are assigned': 'затем будут определены спикеры',
        'Stop (same as Ctrl+C)': 'Остановить (как Ctrl+C)',
        'Stop': 'Стоп',
        'Stop recording': 'Остановить запись',
        'Unmute the microphone and continue': 'Включить микрофон и продолжить',
        'Mute the microphone; the session keeps running': 'Выключить микрофон; сессия продолжается',
        'Resume': 'Продолжить',
        'Pause': 'Пауза',
        'PAUSED': 'ПАУЗА',
        'REC': 'ЗАПИСЬ',
        'Speech recognition engine': 'Движок распознавания речи',
        'Detected language': 'Определённый язык',
        'Speakers are numbered live and refined after the session': 'Спикеры нумеруются сразу и уточняются после сессии',
        'SPEAKERS': 'СПИКЕРЫ',
        'Recording in': 'Запись идёт в',
        'Stop automatic frames': 'Остановить автокадры',
        'Take a screenshot of the capture area': 'Сделать скриншот области захвата',
        'How long a silence ends a phrase. Shorter: text appears sooner, in smaller pieces. Longer: fewer, longer chunks. Can be changed while recording.':
          'Сколько должна длиться тишина, чтобы фраза закончилась. Короче - текст появляется быстрее, мелкими кусками. Длиннее - кусков меньше, но они длиннее. Можно менять во время записи.',
        'Phrase gap': 'Пауза фразы',
        'Stopping': 'Остановка',
        'Pick the app whose audio to transcribe': 'Выберите приложение, звук которого нужно распознавать',
        'Choose an audio file first': 'Сначала выберите аудиофайл',

        // Audio source
        'Microphone': 'Микрофон',
        'App + mic': 'Приложение + микрофон',
        'App only': 'Только приложение',
        'Audio file': 'Аудиофайл',
        'Video file (real-time player)': 'Видеофайл (плеер, реальное время)',
        'Video file (background)': 'Видеофайл (в фоне)',
        'Transcribe video': 'Распознать видео',
        'Play & transcribe': 'Смотреть и распознавать',
        'Choose a video file first': 'Сначала выберите видеофайл',
        "Video file to transcribe (stored in the project's audio folder)": 'Видеофайл для распознавания (хранится в папке audio проекта)',
        'Set start': 'Начало',
        'Set end': 'Конец',
        'Whole file': 'Весь файл',
        'end': 'конец',
        'Click to move the player here': 'Щёлкните, чтобы перейти сюда',
        "Start transcribing at the player's position": 'Начать распознавание с позиции плеера',
        "Stop transcribing at the player's position": 'Закончить распознавание на позиции плеера',
        'Transcribe the whole file': 'Распознать весь файл',
        'continue from there': 'продолжить с этого места',
        'Plays from the start mark in real time; Ctrl+Shift+S takes the video frame, not a screenshot':
          'Воспроизводится с отметки начала в реальном времени; Ctrl+Shift+S берёт кадр из видео, а не скриншот',
        'Faster than real time in the background; auto frames come straight from the video':
          'Быстрее реального времени, в фоне; авто-кадры берутся прямо из видео',
        'Faster than real time': 'Быстрее реального времени',
        'Read faster than real time, not played · the picture shows where the recognizer is':
          'Читается быстрее реального времени, без воспроизведения · кадр показывает, где сейчас распознавание',
        'Use seconds, M:SS or H:MM:SS': 'Укажите секунды, М:СС или Ч:ММ:СС',
        'The end must be after the start': 'Конец должен быть после начала',
        'Continue reading the file': 'Продолжить чтение файла',
        'Pause reading the file; the session keeps running': 'Приостановить чтение файла; сессия продолжается',
        'Save the video frame on screen (full resolution)': 'Сохранить кадр видео с экрана (в полном разрешении)',
        'Loading apps…': 'Загрузка приложений…',
        'App audio is unavailable': 'Звук приложений недоступен',
        'The app whose audio to transcribe (● = playing sound now)': 'Приложение, звук которого распознавать (● = сейчас воспроизводит звук)',
        'No apps with audio': 'Нет приложений со звуком',
        'All system audio': 'Весь системный звук',
        '⚠ All apps are recorded': '⚠ Пишутся все приложения',
        "This Windows can't record a single app (it needs Windows 10 2004+ with process loopback, or Windows 11): all system audio is recorded - mute other sounds":
          'Эта версия Windows не умеет записывать одно приложение (нужна Windows 10 2004+ с process loopback или Windows 11): записывается весь системный звук - выключите лишние звуки',
        'Refresh the list of apps': 'Обновить список приложений',
        "Audio file to transcribe (stored in the project's audio folder)": 'Аудиофайл для распознавания (хранится в папке audio проекта)',
        'Upload another file…': 'Загрузить другой файл…',
        'Choose a file…': 'Выбрать файл…',
        'Source': 'Источник',
        'What to transcribe': 'Что распознавать',
        'FILE': 'ФАЙЛ',
        'App audio → Them, microphone → Me': 'Звук приложения → Собеседник, микрофон → Я',
        'App audio only': 'Только звук приложения',

        // Pipeline status
        'Paused': 'Пауза',
        'microphone muted': 'микрофон выключен',
        'audio muted': 'звук выключен',
        'session continues': 'сессия продолжается',
        'recording': 'запись',
        'in the file': 'в файле',
        'End this phrase now and send it to the recognizer, without waiting for the pause': 'Закончить фразу сейчас и отправить на распознавание, не дожидаясь паузы',
        'Cut': 'Отрезать',
        'Reading the file': 'Читаю файл',
        'Listening': 'Слушаю',
        'waiting for speech': 'жду речь',
        'Up to date': 'Всё распознано',
        'all speech transcribed': 'вся речь расшифрована',
        'Whisper is working through this chunk; text appears as it is recognized.': 'Whisper обрабатывает этот фрагмент; текст появляется по мере распознавания.',
        'Queued': 'В очереди',
        'next': 'следующий',
        'Finishing…': 'Завершение…',
        'transcribing the remaining speech': 'распознаю оставшуюся речь',
        'loading models': 'загрузка моделей',
        'microphone is muted': 'микрофон выключен',
        'press Resume to continue': 'нажмите «Продолжить», чтобы возобновить',
        'Speaking': 'Идёт речь',
        'Listening…': 'Слушаю…',
        'now': 'сейчас',

        // ── Transcript ──
        'Hide this line: it stays here, faded, but Beautify leaves it out': 'Скрыть строку: она останется здесь бледной, но в оформление не попадёт',
        'Show this line again': 'Снова показывать строку',
        'Hide this screenshot: Beautify leaves it out': 'Скрыть скриншот: в оформление он не попадёт',
        'Use this screenshot again': 'Снова использовать скриншот',
        'Hide this frame: Beautify leaves it out': 'Скрыть кадр: в оформление он не попадёт',
        'Use this frame again': 'Снова использовать кадр',
        'Hide this line and the ones like it (same or nearly the same words)': 'Скрыть эту строку и похожие (те же или почти те же слова)',
        'Hide this picture and every one that looks like it (screenshots and frames)': 'Скрыть это изображение и все похожие (скриншоты и кадры)',
        'Hide this frame and every picture that looks like it (frames and screenshots)': 'Скрыть этот кадр и все похожие изображения (кадры и скриншоты)',
        'Hid this line (no similar ones)': 'Строка скрыта (похожих нет)',
        'Already hidden': 'Уже скрыто',
        'Undo': 'Отменить',
        'Looking for similar pictures…': 'Ищу похожие изображения…',
        'Screenshot': 'Скриншот',
        'Selected text': 'Выделенный текст',
        'Me': 'Я',
        'Them': 'Собеседник',
        'Create a project to begin': 'Создайте проект, чтобы начать',
        'Projects keep transcripts, screenshots and documents together.': 'Проект хранит транскрипты, скриншоты и документы вместе.',
        'Ready when you are': 'Всё готово',
        'Choose what screenshots should capture (top right), then press': 'Выберите, что снимать на скриншотах (справа вверху), затем нажмите',
        '. Speech appears here as it is recognized; press': '. Речь появляется здесь по мере распознавания; нажмите',
        'or': 'или',
        'to capture the screen.': ', чтобы снять экран.',

        // ── Screenshots ──
        'Captured': 'Снято',
        'Delete screenshot': 'Удалить скриншот',
        'No screenshots yet': 'Скриншотов пока нет',
        'Press': 'Нажмите',
        "while recording. Each screenshot is OCR'd and used by Beautify to fix names and identify speakers.": 'во время записи. Текст каждого скриншота распознаётся (OCR) и используется при оформлении, чтобы исправить имена и определить спикеров.',
        'Delete this screenshot?': 'Удалить этот скриншот?',
        'Delete': 'Удалить',
        'Screenshot deleted': 'Скриншот удалён',
        'kept in screenshots/.trash': 'сохранён в screenshots/.trash',
        'Find in transcript': 'Найти в транскрипте',
        'Describe': 'Описать',
        'Describing…': 'Описываю…',
        'Described': 'Описан',
        'described': 'описан',
        'Describe: a vision model writes what is on this image; Beautify uses it instead of the OCR text': 'Описать: визуальная модель опишет, что на картинке; при оформлении это описание заменит текст OCR',
        'Described · click to describe again': 'Описан · нажмите, чтобы описать заново',
        'Described · Beautify will use it instead of the OCR text': 'Описано · при оформлении будет использовано вместо текста OCR',
        'Described by the vision model: Beautify uses it instead of the OCR text': 'Описан визуальной моделью: при оформлении используется вместо текста OCR',
        'The text on it is recognized (OCR)': 'Текст на нём распознан (OCR)',
        'OCR is not ready yet.': 'OCR ещё не готов.',
        'Screen descriptions': 'Описания экрана',
        'Vision model': 'Визуальная модель',
        'The Describe button next to each screenshot and auto frame sends the image to this model. The same key is used if you pick this provider for Beautify.': 'Кнопка «Описать» у каждого скриншота и автокадра отправляет картинку этой модели. Если выбрать этого провайдера для оформления, будет использован тот же ключ.',
        'Screen notes failed: using the screenshots\' OCR text (see Log)': 'Заметки об экране не получились: используется OCR скриншотов (см. лог)',
        'Loading OCR text…': 'Загрузка текста OCR…',
        'Click again to delete': 'Нажмите ещё раз для удаления',
        '(no text recognized)': '(текст не распознан)',
        'OCR is not ready yet.': 'OCR ещё не готов.',
        'OCR is not available.': 'OCR недоступен.',

        // ── Auto frames ──
        'frames →': 'кадров →',
        'scenes': 'сцен',
        'smart filter loading': 'загружается умный фильтр',
        'idle': 'ожидание',
        'video': 'видео',
        'moving': 'движение',
        'still': 'статично',
        'settling': 'стабилизация',
        'Watching the screen…': 'Слежу за экраном…',
        'Auto frames are off': 'Автокадры выключены',
        'Stop auto frames': 'Остановить автокадры',
        'Start auto frames': 'Включить автокадры',
        'Visible frames go to Beautify as screenshots, at their place in the transcript': 'Видимые кадры попадают в оформление как скриншоты, на своё место в транскрипте',
        'Best': 'Лучшие',
        'Keep the N frames with the most said about them (pointing phrases, time on screen) and hide the rest. The eye brings any frame back.':
          'Оставить N кадров, о которых говорили больше всего (указательные фразы, время на экране), остальные скрыть. Кнопка с глазом вернёт любой кадр.',
        'Pick': 'Отобрать',
        'Changed while on screen; the card shows the last state': 'Менялся, пока был на экране; карточка показывает последнее состояние',
        'updated': 'обновлён',
        'scroll': 'прокрутка',
        'Show in the transcript': 'Показать в транскрипте',
        'Open': 'Открыть',
        'Picked': 'Отобран',
        'No auto frames': 'Автокадров нет',
        'Turn on': 'Включите',
        'before or during a recording. The screen is captured every second; only frames that show something new and stay still are kept, talking heads and transitions are skipped.':
          'до или во время записи. Экран снимается каждую секунду; сохраняются только кадры с новым содержимым, которые какое-то время не меняются, а говорящие головы и переходы пропускаются.',
        'first and last state': 'первое и последнее состояние',

        // ── Beautify ──
        'update': 'обновление',
        'full rebuild': 'полная пересборка',
        'final': 'финальная',
        'Final': 'Финальная',
        'Final version ready': 'Финальная версия готова',
        'Beautification finished': 'Оформление готово',
        'Open the beautified version': 'Открыть оформленную версию',
        'AI is not set up': 'ИИ не настроен',
        'Beautify': 'Оформить',
        'More options': 'Ещё варианты',
        'Update with new material': 'Дополнить новым материалом',
        'fastest': 'быстрее всего',
        'Full rebuild': 'Полная пересборка',
        'Regenerates the whole document from the transcript': 'Заново создаёт весь документ из транскрипта',
        'Final version': 'Финальная версия',
        'Definitive document with every speaker identified; also saved to docs/': 'Окончательный документ со всеми определёнными спикерами; также сохраняется в docs/',
        'Available after the recording stops (runs automatically)': 'Доступно после остановки записи (запускается автоматически)',
        'Change the provider (Claude Code, Gemini, OpenAI, …), API key, model': 'Сменить провайдера (Claude Code, Gemini, OpenAI, …), API-ключ, модель',
        'Layout': 'Как оформлять',
        'Model writes the whole document': 'Модель пишет весь документ',
        'Header, styles and footer too · for strong models': 'Вместе с шапкой, стилями и футером · для сильных моделей',
        'App builds the frame': 'Каркас собирает приложение',
        'The model writes only the conversation · for small local models': 'Модель пишет только разговор · для маленьких локальных моделей',
        'Recording finished': 'Запись завершена',
        'transcript saved': 'транскрипт сохранён',
        'Build the final beautified version of': 'Собрать финальную оформленную версию сессии',
        'now? Hidden lines, screenshots and frames are left out. You can always start it later with': 'сейчас? Скрытые строки, скриншоты и кадры не войдут. Запустить можно и позже кнопкой',
        'Not now': 'Не сейчас',
        'Build final version': 'Собрать финальную версию',
        'No beautified version yet': 'Оформленной версии пока нет',
        'Beautify turns the raw transcript and screenshots into a full annotated document: every utterance kept, real speakers identified, callouts compared with what you already know.':
          '«Оформить» превращает сырой транскрипт и скриншоты в полный документ с аннотациями: каждая реплика сохранена, настоящие спикеры определены, выноски сопоставлены с тем, что вы уже знаете.',
        'Beautify now': 'Оформить сейчас',
        'turns': 'реплик',
        'callouts': 'выносок',
        'screenshots': 'скриншотов',
        'covers': 'охватывает',
        "Copy saved in the project's docs/ folder": 'Копия, сохранённая в папке docs/ проекта',
        'docs/ copy': 'копия в docs/',

        // ── Chat ──
        'Something went wrong': 'Что-то пошло не так',
        'Summarize the discussion so far': 'Кратко перескажи обсуждение',
        'List decisions and action items': 'Перечисли решения и задачи',
        'Who is speaking, and what are their roles?': 'Кто говорит и какие у них роли?',
        'What open questions should I follow up on?': 'Какие открытые вопросы стоит уточнить?',
        'Ask about this session': 'Спросите об этой сессии',
        'Answers come from the live transcript.': 'Ответы основаны на текущем транскрипте.',
        'Answers come from the live transcript and its screenshots.': 'Ответы основаны на текущем транскрипте и его скриншотах.',
        'Answers come from the saved transcript.': 'Ответы основаны на сохранённом транскрипте.',
        'Answers come from the saved transcript and its screenshots.': 'Ответы основаны на сохранённом транскрипте и его скриншотах.',
        'Try': 'Попробуйте',
        'Set up': 'Настроить',

        // ── Capture area ──
        'Screenshot area': 'Область скриншота',
        'What': 'Область, которую снимают',
        'and Snap capture. Changes apply immediately, even mid-recording, so this window can stay out of your screenshots.':
          'и «Снимок». Изменения применяются сразу, даже во время записи, так что это окно может не попадать на скриншоты.',
        'main display': 'основной дисплей',
        'drag to select': 'выделите мышью',
        'click a window': 'щёлкните по окну',
        'Saving…': 'Сохранение…',
        'Watching the capture area for ~6 s': 'Наблюдаю за областью захвата ~6 с',
        'keep the video playing': 'не останавливайте видео',
        'Drag on screen to select the area': 'Выделите область на экране',
        'Esc to cancel': 'Esc - отмена',
        'Click the window to capture': 'Щёлкните по окну, которое нужно снимать',
        'Capture preview': 'Предпросмотр захвата',
        'No preview yet': 'Предпросмотра пока нет',
        'Choose from window list': 'Выбрать из списка окон',
        'Refresh preview': 'Обновить предпросмотр',
        'Auto frames:': 'Автокадры:',
        'the framed part only': 'только часть в рамке',
        'drag to move, edges to resize': 'перетаскивайте рамку, края - для изменения размера',
        'Detect again': 'Определить заново',
        'Use whole area': 'Вся область',
        "Auto frames use the whole area. If the video is only part of it (a browser or call window), detect it so slide changes aren't drowned out by the page.":
          'Автокадры используют всю область. Если видео занимает лишь её часть (окно браузера или звонка), найдите его, чтобы смена слайдов не терялась на фоне страницы.',
        'Set by hand': 'Задать вручную',
        'Detect video': 'Найти видео',
        'Loading windows…': 'Загрузка окон…',
        'No windows found': 'Окна не найдены',
        'Drag to move': 'Перетащите, чтобы сдвинуть',
        'drag an edge or corner to resize': 'потяните за край или угол, чтобы изменить размер',
        'Drag the frame onto the video': 'Перетащите рамку на видео',
        'Auto frames will keep only the video area': 'Автокадры будут сохранять только область видео',
        'Selection cancelled': 'Выбор отменён',
        'manual': 'вручную',

        // ── Settings ──
        'AI for Beautify and chat': 'ИИ для оформления и чата',
        'Beautify model': 'Модель оформления',
        'Chat model': 'Модель чата',
        'Offer to build the final version when recording stops': 'Предлагать собрать финальную версию после остановки записи',
        'Knowledge folder': 'Папка знаний',
        'Claude Code runs in this folder, so the MCP servers configured there (e.g.': 'Claude Code запускается в этой папке, поэтому настроенные там MCP-серверы (например,',
        ') power the NEW / CONFIRMS / UPDATES callouts of Update runs.': ') дают выноски NEW / CONFIRMS / UPDATES при обновлении (Update).',
        'Style reference': 'Образец стиля',
        'Built-in annotated-transcript style': 'Встроенный стиль аннотированного транскрипта',
        'An HTML document whose look to copy (style only, never its content).': 'HTML-документ, оформление которого нужно повторить (только стиль, не содержимое).',
        'Context and corrections live in the': 'Контекст и исправления хранятся на вкладке',
        'tab (': '(',
        'in the project folder).': 'в папке проекта).',
        'Recognizer': 'Распознаватель',
        'Same as the server': 'Тот же, что у сервера',
        'Check': 'Проверить',
        'The interpreter that has faster-whisper, torch and easyocr installed.': 'Интерпретатор, в котором установлены faster-whisper, torch и easyocr.',
        'Smart filter (CLIP): drop webcam faces, merge look-alike frames': 'Умный фильтр (CLIP): отбрасывать лица с веб-камеры, объединять похожие кадры',
        'Applies to the next recording. Off = pixel comparison only.': 'Применяется к следующей записи. Выкл = только попиксельное сравнение.',
        'Save': 'Сохранить',
        'Settings saved': 'Настройки сохранены',

        // ── AI provider picker ──
        'Installed': 'Установлен',
        'best results: sees screenshots, uses your MCP servers': 'лучший результат: видит скриншоты, использует ваши MCP-серверы',
        'Not installed on this computer (': 'Не установлен на этом компьютере (',
        'get it': 'установить',
        'Key from environment': 'Ключ из переменных окружения',
        'vLLM, llama.cpp, any OpenAI-style API': 'vLLM, llama.cpp, любой API в стиле OpenAI',
        'Runs on this computer': 'Работает на этом компьютере',
        'no key': 'без ключа',
        'Needs an API key': 'Нужен API-ключ',
        'Test Claude Code': 'Проверить Claude Code',
        'API key': 'API-ключ',
        'Host (base URL)': 'Хост (base URL)',
        "OpenAI-compatible endpoint. Leave empty for the provider's default. Local servers: /v1 is added when there is no path.":
          'OpenAI-совместимый адрес. Оставьте пустым, чтобы использовать адрес провайдера по умолчанию. Для локальных серверов /v1 добавляется сам, если путь не указан.',
        'The server answered, but not in the OpenAI format: check the host (base URL). OpenAI-compatible servers usually end with /v1, e.g. http://localhost:1234/v1':
          'Сервер ответил, но не в формате OpenAI: проверьте хост (base URL). Адрес OpenAI-совместимого сервера обычно заканчивается на /v1, например http://localhost:1234/v1',
        'Model': 'Модель',
        'Test': 'Проверить',
        "With an API, Beautify is one pass over the transcript and the screenshots' text (no images, no tools); chat sees the transcript and your notes.":
          'Через API оформление - это один проход по транскрипту и тексту скриншотов (без изображений и инструментов); чат видит транскрипт и ваши заметки.',
        'Using the key from the environment': 'Используется ключ из переменных окружения',
        'Optional for local servers': 'Необязателен для локальных серверов',
        'Paste your API key': 'Вставьте API-ключ',
        'Get one at': 'Получить ключ:',
        '. Stored on this computer only.': '. Хранится только на этом компьютере.',
        'Stored on this computer only.': 'Хранится только на этом компьютере.',
        'Ollama (local)': 'Ollama (локально)',
        'LM Studio (local)': 'LM Studio (локально)',
        'Other (OpenAI-compatible)': 'Другой (OpenAI-совместимый)',
        'Device': 'Устройство',
        'Auto (GPU when available)': 'Авто (GPU, если есть)',
        'GPU (CUDA)': 'GPU (CUDA)',
        'CPU': 'CPU',
        "Where Whisper and the speaker model run. An NVIDIA GPU makes Whisper several times faster; it falls back to the CPU if CUDA doesn't load.":
          'Где работают Whisper и модель голосов. С видеокартой NVIDIA Whisper в разы быстрее; если CUDA не загрузится, используется CPU.',
        'Screenshots are off - pip install mss pillow': 'Скриншоты выключены - выполните pip install mss pillow',
        'Screenshots are off: the screenshot helper is missing': 'Скриншоты выключены: нет помощника скриншотов',
        'model name': 'название модели',
        'Asking Claude…': 'Спрашиваю Claude…',
        'Checking…': 'Проверка…',
        'Enter the API key': 'Введите API-ключ',
        'Enter the host (base URL)': 'Введите хост (base URL)',
        'Enter the model name (Test lists the available ones)': 'Введите название модели («Проверить» покажет доступные)',
        'Welcome to ushi': 'Добро пожаловать в ushi',
        'Recording and transcription work fully offline. To turn transcripts into annotated documents (Beautify) and to chat about them, choose an AI. You can change it later in Settings.':
          'Запись и распознавание работают полностью офлайн. Чтобы превращать транскрипты в аннотированные документы («Оформить») и обсуждать их в чате, выберите ИИ. Его можно сменить позже в настройках.',
        'Beautify and chat use this AI. Recording does not need it.': 'Этот ИИ используется для оформления и чата. Для записи он не нужен.',
        'Later': 'Позже',

        // ── Notes & external transcripts ──
        'Saved': 'Сохранено',
        'Not saved': 'Не сохранено',
        'Editing…': 'Редактирование…',
        'Drop the Teams transcript of this meeting here (.vtt or .docx). Beautify takes speaker names from it and keeps our spelling of brands and terms.':
          'Перетащите сюда транскрипт этой встречи из Teams (.vtt или .docx). Оформление возьмёт из него имена спикеров и сохранит наше написание брендов и терминов.',
        'All sessions': 'Все сессии',
        'This session': 'Эта сессия',
        'Used for every session in this project. Click to use it for this session only.': 'Используется для всех сессий проекта. Нажмите, чтобы использовать только для этой сессии.',
        'Used when beautifying this session. Click to use it for every session.': 'Используется при оформлении этой сессии. Нажмите, чтобы использовать для всех сессий.',
        'Belongs to another session. Click to use it for this session.': 'Относится к другой сессии. Нажмите, чтобы использовать для этой.',
        'Remove': 'Удалить',

        // ── Correction popover ──
        'Who said it': 'Кто сказал',
        'Correct the transcript': 'Исправить транскрипт',
        'Fix text': 'Исправить текст',
        'Saved to Notes as': 'Сохраняется в заметки как',
        ', so Beautify attributes this line (and that voice) to the person.': ', чтобы при оформлении эта реплика (и этот голос) была приписана этому человеку.',
        'Saved to Notes as a rule, so Beautify and Ask apply it everywhere, and live recognition uses it as a hint.':
          'Сохраняется в заметки как правило: «Оформить» и «Спросить» применяют его везде, а распознавание во время записи использует как подсказку.',
        'Quote': 'Цитата',
        'Said by': 'Кто сказал',
        'e.g. Jane Doe': 'например, Иван Петров',
        'save ·': 'сохранить ·',
        'close': 'закрыть',
        'Add to notes': 'Добавить в заметки',
        'Heard': 'Распознано',
        'Should be': 'Должно быть',
        'Comment': 'Комментарий',
        '(optional)': '(необязательно)',
        "e.g. product name, person's name": 'например, название продукта, имя человека',

        // ── Boot ──
        'Screenshots are off - pip install mss pillow': 'Скриншоты отключены - pip install mss pillow',
        'screenshot_helper binary is missing - run ./compile.sh': 'Нет бинарника screenshot_helper - запустите ./compile.sh',

        // ── Server messages (server.py, main.py, llm_api.py, beautify.py, claude_cli.py, sessions.py, external.py …) ──
        'A recording is already running': 'Запись уже идёт',
        'GigaAM recognizes Russian only; pick Auto/RU or another engine': 'GigaAM распознаёт только русский; выберите Авто/RU или другой движок',
        'The session to continue was not found': 'Сессия для продолжения не найдена',
        'Not recording': 'Запись не идёт',
        'Beautify is already running for this session': 'Оформление этой сессии уже выполняется',
        'Still answering the previous question': 'Ещё отвечаю на предыдущий вопрос',
        'Starting recognizer': 'Запуск распознавателя',
        'Starting capture': 'Запуск записи',
        "Show this version's HTML file in its folder": 'Показать HTML-файл этой версии в папке',
        'Embed images': 'Встроить картинки',
        'Save this version as one HTML file with the images embedded (vN-emb.html), to share without the image folders':
          'Сохранить эту версию одним HTML-файлом со встроенными картинками (vN-emb.html), чтобы делиться без папок с картинками',
        'Models loaded': 'Модели загружены',
        'Ready to record': 'Можно начинать запись',
        'Models are loaded · recording starts when you press Start': 'Модели загружены · запись начнётся, когда нажмёте «Начать запись»',
        'Models are loaded. Recording has not started yet: start it now?': 'Модели загружены. Запись ещё не началась - начать сейчас?',
        'Ready': 'Готово',
        'models are loaded · press Start recording': 'модели загружены · нажмите «Начать запись»',
        'Recording stops on its own after this many minutes. Can be changed while recording.': 'Через сколько минут запись остановится сама. Можно менять во время записи.',
        'Session finished': 'Сессия завершена',
        'Speakers assigned': 'Спикеры определены',
        'Diarization': 'Разделение по спикерам',
        'Updating the previous version': 'Обновляю предыдущую версию',
        'Rebuilding the full document': 'Пересобираю документ целиком',
        'Building the final version': 'Собираю финальную версию',
        'project knowledge connected': 'знания проекта подключены',
        'Finished': 'Готово',
        'Failed': 'Ошибка',
        'Cancelled': 'Отменено',
        'Beautify failed': 'Оформление не удалось',
        'Chat failed': 'Ошибка чата',
        'Claude Code (the claude CLI) is not installed': 'Claude Code (CLI claude) не установлен',
        "Claude Code CLI ('claude') not found in PATH": "Claude Code CLI ('claude') не найден в PATH",
        'No answer in 90 s': 'Нет ответа за 90 с',
        'Bad engine': 'Неверный движок',
        'Unknown provider': 'Неизвестный провайдер',
        'Empty question': 'Пустой вопрос',
        'Nothing to correct': 'Нечего исправлять',
        'Both the quote and the speaker are needed': 'Нужны и цитата, и спикер',
        'Bad mode': 'Неверный режим',
        'Bad pause value': 'Неверное значение паузы',
        'Bad visibility change': 'Неверное изменение видимости',
        'Unknown picture': 'Неизвестное изображение',
        'Bad capture mode': 'Неверный режим захвата',
        'Bad crop': 'Неверная рамка',
        'Could not capture the screen (check Screen Recording permission)': 'Не удалось снять экран (проверьте разрешение на запись экрана)',
        'No video found: nothing was changing. Play the video and try again.': 'Видео не найдено: ничего не менялось. Запустите видео и попробуйте снова.',
        "Only a small part of the window was changing (probably the speaker's webcam). Wait for the next slide and try again, or select the area by hand.":
          'Менялась лишь небольшая часть окна (вероятно, веб-камера докладчика). Дождитесь следующего слайда и попробуйте снова или выделите область вручную.',
        'Loading Silero VAD': 'Загрузка Silero VAD',
        'Loading speaker model': 'Загрузка модели спикеров',
        'Decoding the audio file': 'Декодирование аудиофайла',
        'auto-stop': 'автостоп',
        'Transcribing remaining speech': 'Распознаю оставшуюся речь',
        'The app audio capture stopped': 'Захват звука приложения остановлен',
        'Screenshots are off: the screenshot helper is missing': 'Скриншоты отключены: нет помощника для скриншотов',
        'no internet connection (the model is downloaded once, on first use)': 'нет подключения к интернету (модель загружается один раз, при первом использовании)',
        'afconvert produced no audio data': 'afconvert не выдал аудиоданных',
        'ffmpeg not found: install it (https://ffmpeg.org) or pip install imageio-ffmpeg': 'ffmpeg не найден: установите его (https://ffmpeg.org) или pip install imageio-ffmpeg',
        'pip install soundcard': 'pip install soundcard',
        'run ./build_audio_helper.sh': 'запустите ./build_audio_helper.sh',
        'Python: expected the path of a python executable': 'Python: нужен путь к исполняемому файлу python',
        'device: auto, cpu or cuda': 'device: auto, cpu или cuda',
        'Style reference: expected an .html file': 'Образец стиля: нужен файл .html',
        'Not a screenshot of this project': 'Это не скриншот этого проекта',
        'The .docx is too large': 'Файл .docx слишком большой',
        'The file is empty': 'Файл пустой',
        'The file is too large': 'Файл слишком большой',
        'No text found in the file': 'В файле не найден текст',
        'No such external transcript': 'Нет такого внешнего транскрипта',
        'Set the API host (base URL)': 'Укажите хост API (base URL)',
        'Choose a model': 'Выберите модель',
        'The API key was rejected': 'API-ключ отклонён',
        "The request doesn't fit the model's context (as loaded on the server: LM Studio loads a model on demand with its default context length; load it yourself with a larger Context Length)": 'Запрос не помещается в контекст модели (так, как она загружена на сервере: LM Studio, загружая модель по запросу, берёт контекст по умолчанию; загрузите её сами с большим Context Length)',
        'Could not reach the API host (internet connection, or the base URL; is the local server running?)': 'Не удалось связаться с хостом API (интернет или base URL; запущен ли локальный сервер?)',
        'The model returned an empty answer': 'Модель вернула пустой ответ',
        // claude_cli.py / beautify.py step labels
        'Reading…': 'Читаю…',
        'Writing document…': 'Пишу документ…',
        'Editing document…': 'Правлю документ…',
        'Editing document': 'Правлю документ',
        'Searching project knowledge…': 'Ищу в знаниях проекта…',
        'Planning': 'Планирую',
        'Reading the transcript': 'Читаю транскрипт',
        'Studying the style reference': 'Изучаю образец стиля',
        'Reading the external transcript': 'Читаю внешний транскрипт',
      },

      // Strings with numbers / names. Tried in order after an exact match fails; a capture group
      // that holds a name, file or quote is passed through untouched.
      patterns: [
        [/^Condensing what was on screen \((\d+) images\)$/, (n) => `Сжимаю заметки о том, что было на экране (${cnt(n, 'картинка', 'картинки', 'картинок')})`],
        [/^Screen notes: (\d+) images → ([\d,]+) characters$/, (n, c) => `Заметки об экране: ${cnt(n, 'картинка', 'картинки', 'картинок')} → ${c} символов`],
        // numbers with units
        [/^(\d+(?:\.\d+)?) s$/, '$1 с'],
        [/^Saved (v\d+-emb\.html) \((\d+(?:\.\d+)?) MB\)$/, 'Сохранено: $1 ($2 МБ)'],
        [/^(\d+)m (\d+)s$/, '$1 мин $2 с'],
        [/^(\d+)s$/, '$1 с'],
        [/^(\d+) min ago$/, '$1 мин назад'],
        [/^just now$/, 'только что'],
        [/^\/ (\d+) min$/, '/ $1 мин'],
        // counts
        [/^(\d+) sessions?$/, (n) => cnt(n, 'сессия', 'сессии', 'сессий')],
        [/^(\d+) lines?$/, (n) => cnt(n, 'строка', 'строки', 'строк')],
        [/^(\d+) screenshots?$/, (n) => cnt(n, 'скриншот', 'скриншота', 'скриншотов')],
        [/^(\d+) frames?$/, (n) => cnt(n, 'кадр', 'кадра', 'кадров')],
        [/^(\d+) turns?$/, (n) => cnt(n, 'реплика', 'реплики', 'реплик')],
        [/^(\d+) speakers?$/, (n) => cnt(n, 'спикер', 'спикера', 'спикеров')],
        [/^(\d+) speakers?: (.+)$/, (n, names) => `${cnt(n, 'спикер', 'спикера', 'спикеров')}: ${names}`],
        [/^(\d+) repeats?$/, (n) => cnt(n, 'повтор', 'повтора', 'повторов')],
        [/^(\d+) faces skipped$/, 'пропущено лиц: $1'],
        // downloads
        [/^Downloading (.+) \(first use\): (.+?)(?: of ~(.+))?$/, (l, d, tot) => `Загрузка ${l} (первый запуск): ${units(d)}${tot ? ` из ~${units(tot)}` : ''}`],
        [/^Could not download (.+?): (.+)$/, (l, e) => `Не удалось загрузить ${l}: ${tr(e)}`],
        [/^(.+) downloaded$/, '$1: загружено'],
        [/^downloads (.+) on first use$/, (s) => `загрузит ${units(s)} при первом использовании`],
        // projects, sources, recorder
        [/^Project “(.+)” created$/, 'Проект «$1» создан'],
        [/^Project · (.+)$/, 'Проект · $1'],
        [/^(.+) recognizes Russian only$/, '$1 распознаёт только русский'],
        [/^Uploading (.+)…$/, 'Загрузка $1…'],
        [/^(.+) is ready\. Press “Transcribe file”\.$/, '$1 готов. Нажмите «Распознать файл».'],
        [/^FILE (.+)$/, 'ФАЙЛ $1'],
        [/^(.+) \(not running\)$/, '$1 (не запущено)'],
        [/^(● )?All system audio \((.+)\)$/, (dot, dev) => `${dot || ''}Весь системный звук (${dev})`],
        [/^Phrase gap set to (.+) s, applies from the next phrase$/, 'Пауза фразы: $1 с, со следующей фразы'],
        [/^Speech ([\d.:]+(?: s)?)$/, (s) => `Речь ${units(s)}`],
        [/^sent after a (.+) s pause$/, 'отправка после паузы $1 с'],
        [/^goes to transcription after a (.+) s pause$/, 'уйдёт на распознавание после паузы $1 с'],
        [/^Transcribing ([\d.:]+(?: s)?)$/, (s) => `Распознаю ${units(s)}`],
        [/^Transcribing ([\d.:]+(?: s)?) of speech$/, (s) => `Распознаю ${units(s)} речи`],
        [/^([\d.:]+(?: s)?) of speech$/, (s) => `${units(s)} речи`],
        [/^(\d+) more queued \((.+)\)$/, (n, s) => `ещё ${n} в очереди (${units(s)})`],
        [/^(\d+) chunks? queued$/, (n) => `${cnt(n, 'фрагмент', 'фрагмента', 'фрагментов')} в очереди`],
        [/^#(\d+) in line$/, '№$1 в очереди'],
        [/^The recognizer's Python can't load its models: (.+)$/, (e) => `Python распознавателя не может загрузить модели: ${tr(e)}`],
        [/^Recognizer exited with code (-?\d+)$/, 'Распознаватель завершился с кодом $1'],
        [/^exit code (-?\d+)$/, 'код выхода $1'],
        [/^Loading Whisper (.+)$/, 'Загрузка Whisper $1'],
        [/^Auto-stop set to (\d+) min$/, 'Автостоп: $1 мин'],
        [/^Already recording for (\d+) min: pick a later auto-stop$/, 'Запись идёт уже $1 мин: выберите автостоп позже'],
        [/^Clustering (\d+) voice samples$/, (n) => `Кластеризация ${cnt(n, 'образца', 'образцов', 'образцов')} голоса`],
        [/^Labelling (\d+) speakers$/, (n) => `Разметка: ${cnt(n, 'спикер', 'спикера', 'спикеров')}`],
        [/^Loading (.+)$/, 'Загрузка $1'],
        [/^Unknown engine: (.+)$/, 'Неизвестный движок: $1'],
        [/^Unknown source: (.+)$/, 'Неизвестный источник: $1'],
        [/^App audio needs audio_helper \((.+)\)$/, (h) => `Для звука приложений нужен audio_helper (${tr(h)})`],
        [/^audio_helper is missing \((.+)\)$/, (h) => `Нет audio_helper (${tr(h)})`],
        [/^Audio file not found: (.+)$/, 'Аудиофайл не найден: $1'],
        [/^Not an audio file this app can read: (.+)$/, 'Этот аудиофайл не поддерживается: $1'],
        [/^Can't read (.+?): (.+)$/, 'Не удалось прочитать $1: $2'],
        [/^CUDA is not usable \((.+)\); recognizing on the CPU$/, 'CUDA недоступна ($1); распознавание на CPU'],
        [/^screenshot text recognition is off: (.+)$/, (e) => `распознавание текста на скриншотах отключено: ${tr(e)}`],
        // "where: message" error toasts from the recognizer (main.py events "error")
        [/^(GPU|app audio|transcription|OCR|screenshots|audio_helper): (.+)$/, (w, m) =>
          `${({ 'app audio': 'звук приложения', transcription: 'распознавание', screenshots: 'скриншоты' })[w] || w}: ${tr(m)}`],
        // visibility
        [/^Hid (\d+) similar lines$/, 'Скрыто похожих строк: $1'],
        [/^Hid (.+) that look alike$/, (s) => `Скрыты похожие: ${s
          .replace(/(\d+) frames?/, (_, n) => cnt(n, 'кадр', 'кадра', 'кадров'))
          .replace(/(\d+) screenshots?/, (_, n) => cnt(n, 'скриншот', 'скриншота', 'скриншотов'))
          .replace(' and ', ' и ')}`],
        // transcript / frames
        [/^Speaker (\d+)$/, 'Спикер $1'],
        [/^Auto frame #(\d+)$/, 'Автокадр №$1'],
        [/^on screen (.+)$/, (d) => `на экране ${dur(d)}`],
        [/^shown (\d+)×$/, 'показан $1×'],
        [/^Beautified v(\d+)$/, 'Оформлено v$1'],
        [/^of (\d+) frames go to Beautify$/, (n) => `из ${n} кадров попадут в оформление`],
        [/^Shown (\d+) times$/, (n) => `Показан ${cnt(n, 'раз', 'раза', 'раз')}`],
        [/^Looks like a scrolled #(\d+)$/, 'Похоже на прокрученный №$1'],
        [/^Level 1 \(CLIP\) guess, (\d+)%$/, 'Догадка уровня 1 (CLIP), $1%'],
        [/^“(\d+) pointers?”$/, (n) => `«${cnt(n, 'указание', 'указания', 'указаний')}»`],
        [/^score (.+)$/, 'оценка $1'],
        [/^frame #(\d+)$/, 'кадр №$1'],
        [/^Shown ([\d:]+)–([\d:]+)$/, 'Показан $1–$2'],
        [/^(\d+)% different from the previous scene$/, 'отличается от предыдущей сцены на $1%'],
        [/^(\d+)% masked as moving \(webcam\)$/, '$1% замаскировано как движущееся (веб-камера)'],
        // The quotes after ": " are transcript text: kept as is.
        [/^(\d+) words spoken · (\d+) pointing phrases?(: [\s\S]*)?$/, (w, d, q) =>
          `произнесено слов: ${w} · ${cnt(d, 'указательная фраза', 'указательные фразы', 'указательных фраз')}${q || ''}`],
        // Beautify
        [/^Beautifying v(\d+)$/, 'Оформление v$1'],
        [/^⚠ v(\d+) failed$/, '⚠ v$1: ошибка'],
        [/^(.+) - click to set up$/, (p) => `${tr(p)} - нажмите, чтобы настроить`],
        [/^Extends v(.+) with new speech and screenshots$/, 'Дополняет v$1 новой речью и скриншотами'],
        [/^AI: (.+)$/, 'ИИ: $1'],
        [/^Beautified (.+) \(v(\d+)\)$/, 'Оформлено: $1 (v$2)'],
        [/^v(\d+) \(final\)$/, 'v$1 (финальная)'],
        [/^Beautify failed: ([\s\S]*)$/, (e) => `Оформление не удалось: ${tr(e)}`],
        [/^(\d+) KB generated$/, 'сгенерировано $1 КБ'],
        [/^of ([\d:]+–[\d:]+)$/, 'из $1'],
        [/^([\d:]+) to generate$/, 'генерация: $1'],
        [/^Beautify is not set up: (.+) \(Settings → AI\)$/, (p) => `Оформление не настроено: ${tr(p)} (Настройки → ИИ)`],
        [/^Chat is not set up: (.+) \(Settings → AI\)$/, (p) => `Чат не настроен: ${tr(p)} (Настройки → ИИ)`],
        // Beautify progress steps (server.py, claude_cli.py, beautify.py)
        [/^Writing the document with (.+)$/, 'Пишу документ с помощью $1'],
        [/^Writing the document · (.+) characters$/, 'Пишу документ · $1 симв.'],
        [/^Part (\d+)\/(\d+) (\(.+?\))( \(continuing\))? · (.+) characters$/,
          (k, n, range, cont, c) => `Часть ${k}/${n} ${range}${cont ? ' (продолжение)' : ''} · ${c} симв.`],
        [/^Header & footer( \(continuing\))? · (.+) characters$/,
          (cont, c) => `Шапка и подвал${cont ? ' (продолжение)' : ''} · ${c} симв.`],
        [/^Document( \(continuing\))? · (.+) characters$/, (cont, c) => `Документ${cont ? ' (продолжение)' : ''} · ${c} симв.`],
        [/^The model got stuck repeating itself \((.+) characters into the reply\); try again, or another model$/,
          'Модель зациклилась и повторяет один и тот же текст ($1 симв. ответа); запустите ещё раз или возьмите другую модель'],
        [/^Claude \((.*)\) started$/, 'Claude ($1) запущен'],
        [/^Reading previous version v(\d+)$/, 'Читаю предыдущую версию v$1'],
        [/^Looking at (.+)$/, 'Смотрю $1'],
        [/^Reading (.+)$/, 'Читаю $1'],
        [/^Writing document \((.+) KB\)$/, 'Пишу документ ($1 КБ)'],
        [/^Editing document \(\+(.+) KB\)$/, 'Правлю документ (+$1 КБ)'],
        [/^Searching files: (.*)$/, 'Поиск по файлам: $1'],
        [/^Project knowledge \((.+?)\): ([\s\S]*)$/, 'Знания проекта ($1): $2'],
        [/^Project knowledge: (.+)$/, 'Знания проекта: $1'],
        [/^Using (.+)$/, 'Использую $1'],
        // chat
        [/^Chat needs AI set up: (.*)$/, (p) => `Для чата нужно настроить ИИ: ${tr(p)}`],
        // capture
        [/^(\d+) × (\d+) at (-?\d+), (-?\d+)$/, '$1 × $2, от точки $3, $4'],
        [/^\((\d+)% × (\d+)%(?:, (.+))?\)$/, (w, h, m) => `(${w}% × ${h}%${m ? ', ' + tr(m) : ''})`],
        [/^Screenshots will capture: (.+)$/, (s) => `Скриншоты будут снимать: ${tr(s)}`],
        // settings / AI picker
        [/^✓ (.+) is ready$/, '✓ $1 готов'],
        [/^✓ Works(?: \(answered “([\s\S]*)”\))?(?: · (\d+) models available)?$/, (r, n) =>
          `✓ Работает${r != null ? ` (ответ: «${r}»)` : ''}${n ? ` · доступно моделей: ${n}` : ''}`],
        [/^✗ ([\s\S]+)$/, (e) => `✗ ${tr(e)}`],
        [/^Key saved \((.+)\)$/, 'Ключ сохранён ($1)'],
        [/^Saved: (.+) - type to replace$/, 'Сохранён: $1 - введите новый, чтобы заменить'],
        [/^Saved, but: ([\s\S]*)$/, (p) => `Сохранено, но: ${tr(p)}`],
        [/^Add the (.+) API key$/, 'Добавьте API-ключ $1'],
        [/^Not found \(wrong model name or host\): ([\s\S]*)$/, 'Не найдено (неверное имя модели или хост): $1'],
        [/^Rate limit or quota exceeded: ([\s\S]*)$/, 'Превышен лимит запросов или квота: $1'],
        [/^(.+): expected a number$/, '$1: нужно число'],
        [/^Invalid name: (.+)$/, 'Недопустимое имя: $1'],
        [/^Unsupported file type (.+): use \.vtt, \.docx, \.txt or \.srt$/, 'Неподдерживаемый тип файла $1: используйте .vtt, .docx, .txt или .srt'],
        // notes / external
        [/^Added (.+?)(?: · (\d+) speakers)?\. Beautify will use it\.$/, (f, n) =>
          `Добавлен ${f}${n ? ` · ${cnt(n, 'спикер', 'спикера', 'спикеров')}` : ''}. Он будет использован при оформлении.`],
        [/^Removed (.+)$/, 'Удалён $1'],
        [/^Quote · (.+)$/, 'Цитата · $1'],
        [/^Added to Notes: “([\s\S]*)” ← (.+)$/, 'Добавлено в заметки: «$1» ← $2'],
        [/^Added to Notes: “([\s\S]*)” → “([\s\S]*)”$/, 'Добавлено в заметки: «$1» → «$2»'],
      ],
    },
  };

  // ── Translator ───────────────────────────────────────────────────────────
  const LS_KEY = 'voicerec.lang';
  const LANGS = ['en', 'ru'];
  const norm = s => String(s).replace(/\s+/g, ' ').trim();

  function detectLang() {
    try {
      const saved = localStorage.getItem(LS_KEY);
      if (LANGS.includes(saved)) return saved;
    } catch (_) { /* storage blocked */ }
    const nav = (typeof navigator !== 'undefined' && (navigator.language || (navigator.languages || [])[0])) || '';
    return /^ru\b/i.test(nav) ? 'ru' : 'en';
  }

  let table = null;     // Map of normalized English → translation
  let patterns = [];
  const cache = new Map();

  function load(lang) {
    const d = I18N[lang];
    table = null; patterns = []; cache.clear();
    if (!d) return;
    table = new Map(Object.entries(d.strings).map(([k, v]) => [norm(k), v]));
    patterns = d.patterns || [];
  }

  // Exact string, then patterns. null when nothing matches.
  function lookup(key) {
    if (!table) return null;
    const hit = table.get(key);
    if (hit != null) return hit;
    for (const [re, rep] of patterns) {
      const m = re.exec(key);
      if (m) return typeof rep === 'function' ? rep(...m.slice(1)) : key.replace(re, rep);
    }
    return null;
  }

  // Translate one string; unknown strings come back unchanged. Strings built from parts
  // ("v3 · update · 5 min ago") are translated part by part around the " · " separators.
  function tr(str) {
    if (!table || str == null) return str;
    const key = norm(str);
    if (!key || !/[A-Za-z]/.test(key)) return str;
    if (cache.has(key)) { const c = cache.get(key); return c == null ? str : c; }
    let out = lookup(key);
    if (out == null && key.includes('·')) {
      const parts = key.split(/(\s*·\s*)/);
      let any = false;
      for (let i = 0; i < parts.length; i += 2) {
        const p = parts[i];
        if (!p || !/[A-Za-z]/.test(p)) continue;
        const r = lookup(p);
        if (r != null) { parts[i] = r; any = true; }
      }
      if (any) out = parts.join('');
    }
    if (cache.size > 4000) cache.clear();
    cache.set(key, out);
    return out == null ? str : out;
  }

  // Node (tests / tooling): expose the dictionary and a translator, nothing else.
  if (typeof module === 'object' && module.exports) {
    module.exports = { I18N, translator(lang) { load(lang); return tr; } };
  }
  if (typeof document === 'undefined') return;

  const lang = detectLang();
  load(lang);
  document.documentElement.lang = lang;
  window.i18n = { lang, langs: LANGS.slice(), t: s => (lang === 'en' ? s : tr(s)), setLang };

  function setLang(l) {
    if (!LANGS.includes(l)) return;
    try { localStorage.setItem(LS_KEY, l); } catch (_) { /* storage blocked */ }
    location.reload();
  }

  // ── What is never translated ─────────────────────────────────────────────
  // User data. A node inside one of these is left alone, unless it is inside a "chrome
  // island" (a UI label that app.js renders inside a data element).
  const DATA = [
    '[data-no-i18n]',
    '#transcript .row:not(.ghost-row) .body > p',   // recognized speech
    '#transcript .sel-quote',                         // selected text (its "Selected text" label is chrome)
    '.chunk-text:not(.muted)',                        // text being recognized
    '.shot-inline:not(.frame-inline) .lbl',           // screenshot file name ("Screenshot" label is chrome)
    '.shot-card .name',                               // screenshot file name
    '#chatLog .msg.user', '#chatLog .msg .md',        // chat messages (both sides)
    '#consoleLog',                                    // recognizer log
    '#notesPath',                                     // notes file path (the notes text is a <textarea> value)
    '.ext-name',                                      // external transcript file name
    '#projectMenu [data-p] .mi-title',                // project names
    '#winList .wo', '#winList .wt',                   // window owners / titles
    '#fixPop .orig', 'datalist',                      // selected transcript text, speaker names
    '#control .state-sub.mono',                       // recognizer error output
    '#control .state-sub > b', '.rec-hint > b',       // project / session names
    '.lb-head b',                                     // lightbox time
  ].join(',');
  const CHROME = ['.sel-quote > .k', '.shot-inline .lbl > b'].join(',');
  const SKIP_TAGS = new Set(['SCRIPT', 'STYLE', 'TEXTAREA', 'CODE', 'PRE', 'KBD', 'NOSCRIPT', 'IFRAME', 'SVG', 'svg']);

  // Mixed elements: only these known UI strings are translated, everything else there is data
  // (log lines, the OCR text, project / window / app / file names).
  const isArea = s => /^\d+ × \d+ at -?\d+, -?\d+$/.test(s);
  const RESTRICTED = [
    ['#lastLog', s => ['Connecting…', 'Connected', 'Disconnected from server - retrying…'].includes(s)],
    ['#lbOcr', s => ['Loading OCR text…', '(no text recognized)', 'OCR is not ready yet.', 'OCR is not available.'].includes(s)],
    ['#projectName', s => s === 'Choose…'],
    ['#captureLabel, .pv-label', s => s === 'Full screen' || isArea(s)],
    ['#appSel', s => s === 'No apps with audio' || / \(not running\)$/.test(s) || /^(● )?All system audio/.test(s)],
    ['#fileSel', s => s === 'Upload another file…'],
  ];
  // Attributes whose value is data although the element itself is UI.
  const ATTR_DATA = '.ext-name, .ftag.hot, #aiClaudeRes, #notesPath';

  // Inside a data container, and not in a UI label within it.
  function inData(el) {
    const d = el.closest(DATA);
    if (!d) return false;
    const c = el.closest(CHROME);
    return !(c && d.contains(c) && !d.matches('[data-no-i18n]'));
  }

  // 'skip' | 'normal' | 'lines' | allow-function
  function modeOf(el) {
    if (!el || SKIP_TAGS.has(el.tagName)) return 'skip';
    if (el.closest('pre, code, textarea, script, style, kbd')) return 'skip';
    if (inData(el)) return 'skip';
    for (const [sel, allow] of RESTRICTED) if (el.closest(sel)) return allow;
    // Frame lightbox info: UI lines, then "OCR:" and the screen text (data).
    if (el.closest('.lb-ocr')) return 'lines';
    return 'normal';
  }

  function translateLines(text) {
    const lines = text.split('\n');
    let changed = false;
    for (let i = 0; i < lines.length; i++) {
      if (lines[i].trim() === 'OCR:') break;
      const r = tr(lines[i]);
      if (r !== lines[i]) { lines[i] = r; changed = true; }
    }
    return changed ? lines.join('\n') : text;
  }

  // Keep the node's own leading / trailing whitespace; drop it before punctuation
  // that Russian attaches to the previous word (", чтобы …").
  function wrap(orig, core, out) {
    const lead = orig.match(/^\s*/)[0], trail = orig.match(/\s*$/)[0];
    return (/^[,.;:)]/.test(out) ? '' : lead) + out + trail;
  }

  const seenText = new WeakMap();   // text node → value we last wrote / checked
  function trText(node) {
    const v = node.nodeValue;
    if (!v || seenText.get(node) === v || !/[A-Za-z]/.test(v)) return;
    seenText.set(node, v);
    const mode = modeOf(node.parentElement);
    if (mode === 'skip') return;
    const core = v.trim();
    let out;
    if (mode === 'lines') out = translateLines(v);
    else if (typeof mode === 'function') out = mode(norm(core)) ? wrap(v, core, tr(core)) : v;
    else out = wrap(v, core, tr(core));
    if (out !== v) { seenText.set(node, out); node.nodeValue = out; }
  }

  const ATTRS = ['title', 'placeholder', 'aria-label', 'alt', 'data-q'];
  const seenAttr = new WeakMap();   // element → {attr: value}
  function trAttr(el, name) {
    const v = el.getAttribute(name);
    if (!v || !/[A-Za-z]/.test(v)) return;
    let seen = seenAttr.get(el);
    if (!seen) seenAttr.set(el, (seen = {}));
    if (seen[name] === v) return;
    seen[name] = v;
    if (el.matches(ATTR_DATA) || inData(el)) return;
    if (name === 'value' && !(el.tagName === 'INPUT' && /^(button|submit|reset)$/i.test(el.type))) return;
    const out = tr(v);
    if (out !== v) { seen[name] = out; el.setAttribute(name, out); }
  }

  const ATTR_SEL = ATTRS.map(a => `[${a}]`).join(',') + ',input[type=button],input[type=submit],input[type=reset]';
  function translateTree(root) {
    if (root.nodeType === 3) { trText(root); return; }
    if (root.nodeType !== 1 && root.nodeType !== 9 && root.nodeType !== 11) return;
    const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
    for (let n = walker.nextNode(); n; n = walker.nextNode()) trText(n);
    const els = root.querySelectorAll ? [...root.querySelectorAll(ATTR_SEL)] : [];
    if (root.nodeType === 1 && root.matches(ATTR_SEL)) els.push(root);
    for (const el of els) {
      for (const a of ATTRS) if (el.hasAttribute(a)) trAttr(el, a);
      if (el.tagName === 'INPUT' && el.hasAttribute('value')) trAttr(el, 'value');
    }
  }

  // ── Language toggle (EN | RU) in the top bar ─────────────────────────────
  function addToggle() {
    const bar = document.querySelector('.top-actions');
    if (!bar || bar.querySelector('.lang-seg')) return;
    const seg = document.createElement('div');
    seg.className = 'seg lang-seg';
    seg.setAttribute('data-no-i18n', '');
    seg.title = lang === 'ru' ? 'Язык интерфейса' : 'Interface language';
    seg.style.cssText = 'padding:1px';
    seg.innerHTML = LANGS.map(l => `<button type="button" data-lang="${l}" class="${l === lang ? 'on' : ''}" style="padding:3px 8px;font-size:12px">${l.toUpperCase()}</button>`).join('');
    seg.addEventListener('click', e => {
      const b = e.target.closest('[data-lang]');
      if (b && b.dataset.lang !== lang) setLang(b.dataset.lang);
    });
    bar.insertBefore(seg, document.getElementById('themeBtn') || bar.firstChild);
  }

  function start() {
    addToggle();
    if (lang === 'en' || !table) return;

    // Native dialogs are not DOM: translate their message.
    const wrapDialog = name => {
      const orig = window[name];
      if (typeof orig !== 'function') return;
      window[name] = function (msg, ...rest) { return orig.call(window, msg == null ? msg : tr(String(msg)), ...rest); };
    };
    ['alert', 'confirm', 'prompt'].forEach(wrapDialog);

    translateTree(document.body);
    const obs = new MutationObserver(muts => {
      for (const m of muts) {
        if (m.type === 'characterData') trText(m.target);
        else if (m.type === 'attributes') trAttr(m.target, m.attributeName);
        else m.addedNodes.forEach(translateTree);
      }
      obs.takeRecords();   // drop the records of our own edits
    });
    obs.observe(document.body, {
      childList: true, subtree: true, characterData: true,
      attributes: true, attributeFilter: ATTRS.concat('value'),
    });
  }

  if (document.body) start();
  else document.addEventListener('DOMContentLoaded', start, { once: true });
})();
