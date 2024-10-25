from typing import Final
from enum import Enum
from telegram import Update, ReplyKeyboardMarkup, KeyboardButton, ReplyKeyboardRemove
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
import traceback, asyncio, logging, os
from dotenv import load_dotenv
import asyncpg, gettext, asyncio
# import aioredis
from hashlib import sha256

# Load environment variables from .env file when running locally
load_dotenv()

TOKEN: Final = os.environ.get('TOKEN')
BOT_USERNAME: Final = '@ITIrinaBot'
DATABASE_URL = os.environ['DATABASE_URL']
HEROKU_APP_NAME = os.environ.get('HEROKU_APP_NAME')
NGROK_URL: Final = 'https://f305-2001-818-ddf8-ae00-d569-7e7-f24b-2db1.ngrok-free.app'

# Generate a hash of your token to use as a webhook path.
secure_path = sha256(os.environ['TOKEN'].encode()).hexdigest()

# Determine the appropriate webhook URL and logging based on environment
if HEROKU_APP_NAME:
    WEBHOOK_URL = f'https://{HEROKU_APP_NAME}.herokuapp.com/webhook/{secure_path}'
    logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
else:
    WEBHOOK_URL = f'{NGROK_URL}/webhook/{secure_path}'
    logging.basicConfig(level=logging.DEBUG, format='%(asctime)s - %(levelname)s - %(message)s')

# A ew global variable to track if the bot is expecting an email
waiting_for_email = {}

# Create a connection pool
async def create_pool():
    try:
        # Heroku requires SSL connections
        ssl_context = 'require' if "localhost" not in os.environ['DATABASE_URL'] else False

        pool = await asyncpg.create_pool(
            dsn=os.environ['DATABASE_URL'],
            ssl=ssl_context,
            min_size=1,
            max_size=20,
        )
        print("Database connection pool created successfully.")
        return pool
    except Exception as e:
        print(f"Failed to create a connection pool: {e}")
        return None

class Section(Enum):
    ITJ = "ITJ"
    ITM = "ITM"
    QAJ = "QAJ"
    # QAJS = "QAJS"
    QAM = "QAM"
    # QAMS = "QAMS"

button_labels = {
    Section.ITJ: 'IT. Junior +',
    Section.ITM: 'IT. Middle +',
    Section.QAJ: 'QA/QC. Junior +',
    # Section.QAJS: 'QA/QC. Situational. Junior +',
    Section.QAM: 'QA/QC. Middle +',
    #Section.QAMS: 'QA/QC. Situational. Middle +',
}

label_to_section = {section.value: label for section, label in button_labels.items()}

def get_translation_function(language_code):
    if language_code == 'en':
        return lambda x: x  # English: return text as is
    elif language_code == 'ru':
        locale_path = 'locales'
        try:
            lang = gettext.translation('messages', localedir=locale_path, languages=[language_code])
            return lang.gettext
        except FileNotFoundError:
            return lambda x: x  # Fallback to English
    else:
        return lambda x: x  # Default to English

# Modify fetch_questions to accept language_code and fetch appropriate text
async def fetch_questions(conn, section, language_code):
    # Fetch all questions and their answers for a given section and language
    logging.debug(f"Querying for section: {section} and language: {language_code}")
    questions_data = await conn.fetch("""
        SELECT 
            q.id as question_id,
            CASE WHEN $2 = 'ru' THEN q.text_ru ELSE q.text END as question_text,
            a.id as answer_id,
            CASE WHEN $2 = 'ru' THEN a.text_ru ELSE a.text END as answer_text,
            a.is_correct,
            CASE WHEN $2 = 'ru' THEN a.explanation_ru ELSE a.explanation END as explanation
        FROM questions q
        JOIN answers a ON q.id = a.question_id
        WHERE q.section = $1
        ORDER BY q.id, a.id
    """, section, language_code)
    logging.debug(f"Executed query for section: {section} with result count: {len(questions_data)}")

    # Organize data into a structured format for easier processing in quiz handling
    questions = {}
    for row in questions_data:
        question_id = row['question_id']
        if question_id not in questions:
            questions[question_id] = {
                'question_id': question_id,
                'question_text': row['question_text'],
                'answers': []
            }
        questions[question_id]['answers'].append({
            'answer_id': row['answer_id'],
            'answer_text': row['answer_text'],
            'is_correct': row['is_correct'],
            'explanation': row['explanation']
        })

    return list(questions.values())

# Commands
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.message.chat_id
    logging.debug(f"start_command called with chat_id={chat_id}")

# Retrieve user's language preference from the database
    async with postgres_pool.acquire() as conn:
        user_language = await conn.fetchval("""
            SELECT language FROM users WHERE chat_id = $1
        """, chat_id)
        if not user_language:
            user_language = 'en'  # Default to English
        context.user_data['language_code'] = user_language

    _ = get_translation_function(user_language)

    # Get a connection from the pool
    async with postgres_pool.acquire() as conn:
        # Check if the user has ongoing progress
        active_section = await check_active_quiz(conn, chat_id)

        if active_section:
            # User has ongoing progress, ask if they want to reset it
            logging.debug(f"User {chat_id} has ongoing progress: {active_section}")
            keyboard = [
                [KeyboardButton(_("Yes, reset progress"))],
                [KeyboardButton(_("No, continue my current session"))]
            ]
            reply_markup = ReplyKeyboardMarkup(keyboard, one_time_keyboard=True, resize_keyboard=True)
            await update.message.reply_text(
                _("Heads up! Starting a new session will reset your progress. Would you like to proceed?"),
                reply_markup=reply_markup
            )
        else:
            # No existing progress, proceed as before
            logging.debug(f"No ongoing progress found for user {chat_id}")
            await reset_and_start_new_session(conn, chat_id, update, context)

async def reset_and_start_new_session(conn, chat_id, update, context: ContextTypes.DEFAULT_TYPE):
    user = update.message.from_user
        # Retrieve language code
    language_code = context.user_data.get('language_code')
    if not language_code:
        # Fetch from the database if not in user_data
        async with postgres_pool.acquire() as conn:
            language_code = await conn.fetchval("""
                SELECT language FROM users WHERE chat_id = $1
            """, chat_id)
            if not language_code:
                language_code = 'en'
            context.user_data['language_code'] = language_code

    _ = get_translation_function(language_code)
    try:
        async with conn.transaction():  # Handle transactions
            await conn.execute("INSERT INTO users (chat_id) VALUES ($1) ON CONFLICT DO NOTHING", (chat_id))
            await conn.execute("""
            INSERT INTO user_details (user_id, first_name, last_name, username, language_code) 
            VALUES (
                (SELECT user_id FROM users WHERE chat_id = $1), 
                $2, $3, $4, $5
            ) ON CONFLICT (user_id) DO NOTHING
        """, chat_id, user.first_name, user.last_name, user.username, user.language_code)
            await conn.execute("""
                UPDATE user_progress SET current_index = NULL, correct_answers = 0, incorrect_answers = 0, skipped_questions = 0
                WHERE user_id = (SELECT user_id FROM users WHERE chat_id = $1)
            """, (chat_id))
    except Exception as e:
        logging.error(f"Error in reset_and_start_new_session: {str(e)}")
        await update.message.reply_text(_("Oops! An error occurred. Please try again."))
    # Display keyboard for section choice as originally implemented
    keyboard = [[KeyboardButton(button_labels[section])] for section in Section]
    # # Create a keyboard with two columns: one for Junior sections and another for Middle sections
    # keyboard = [
    #         KeyboardButton(button_labels[Section.ITJ]), 
    #         KeyboardButton(button_labels[Section.ITM]),
    #         KeyboardButton(button_labels[Section.QAJ]), 
    #         KeyboardButton(button_labels[Section.QAM])
    # ]
    
    reply_markup = ReplyKeyboardMarkup(keyboard, one_time_keyboard=True)
    await update.message.reply_text(
        _("👋 Select a section to start practicing:\n\n"
        "- <b>IT. Junior +</b>: Essential IT knowledge.\n"
        "- <b>IT. Middle +</b>: Advanced IT knowledge.\n"
        "- <b>QA/QC. Junior +</b>: Quality Assurance and Quality Control basics.\n"
        "- <b>QA/QC. Middle +</b>:  In-depth expertise in Quality Assurance and Quality Control."),
        # "\nYou can use Skip Question button if the task doesn't correspond to your CV",
        parse_mode='HTML',
        reply_markup=reply_markup
    )
async def set_language_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.message.chat_id
    logging.debug(f"set_language_command called with chat_id={chat_id}")

    # Retrieve user's current language preference from the database
    async with postgres_pool.acquire() as conn:
        user_language = await conn.fetchval("""
            SELECT language FROM users WHERE chat_id = $1
        """, chat_id)
        if not user_language:
            user_language = 'en'  # Default to English
        context.user_data['language_code'] = user_language

        # Check if the user has an active quiz session
        active_section = await check_active_quiz(conn, chat_id)

    # Get the per-user translation function
    _ = get_translation_function(user_language)

    if active_section:
        section = active_section['section']
        index = active_section['current_index']
        logging.debug(f"User {chat_id} is in the middle of a quiz (section: {section}, index: {index})")

        # Inform the user that their progress will be saved
        await update.message.reply_text(
            _("I will save your progress, and you can continue after setting your language preference.")
        )

    # Define the keyboard layout with language options
    keyboard = [
        [KeyboardButton("English")],
        [KeyboardButton("Русский")]
    ]
    reply_markup = ReplyKeyboardMarkup(keyboard, one_time_keyboard=True)
    
    # Send a message asking the user to choose their language
    await update.message.reply_text(_("Select your language / Выберите язык:"), reply_markup=reply_markup)
    
    # Store the active quiz session in context to resume later
    context.user_data['active_section'] = active_section

async def section_command(update: Update, context: ContextTypes.DEFAULT_TYPE, section_str: str):
    chat_id = update.message.chat_id
    logging.debug(f"Starting section_command with chat_id={chat_id} and section={section_str}")
    # Retrieve language code
    language_code = context.user_data.get('language_code')
    if not language_code:
        # Fetch from the database if not in user_data
        async with postgres_pool.acquire() as conn:
            language_code = await conn.fetchval("""
                SELECT language FROM users WHERE chat_id = $1
            """, chat_id)
            if not language_code:
                language_code = 'en'
            context.user_data['language_code'] = language_code

    _ = get_translation_function(language_code)
    # Reset the current index to 0 when a section is chosen and update the database
    # Get a connection from the pool
    async with postgres_pool.acquire() as conn:
        try:
            async with conn.transaction():
                await conn.execute("""
                    INSERT INTO user_progress (user_id, section, current_index)
                    VALUES ((SELECT user_id FROM users WHERE chat_id = $1), $2, 0)
                    ON CONFLICT (user_id, section) DO UPDATE SET current_index = 0
                """, chat_id, section_str)
                # Fetch questions based on the section and language
                questions = await fetch_questions(conn, section_str, language_code)
        except Exception as e:
            logging.error(f"Error in section_command for chat_id={chat_id}, section={section_str}: {str(e)}")
            await update.message.reply_text(_("Something went wrong. Let's try that again."))
    if questions:
        await send_question(update, context, chat_id, questions, section_str)
    else:
        logging.error(f"No questions found for section: {section_str}")
        await update.message.reply_text(_("This section isn't available right now. Please select a different one."))

async def subscribe_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.message.chat_id
    logging.debug(f"subscribe_command called with chat_id={chat_id}")

    # Retrieve language code
    language_code = context.user_data.get('language_code')
    if not language_code:
        # Fetch from the database if not in user_data
        async with postgres_pool.acquire() as conn:
            language_code = await conn.fetchval("""
                SELECT language FROM users WHERE chat_id = $1
            """, chat_id)
            if not language_code:
                language_code = 'en'
            context.user_data['language_code'] = language_code

    _ = get_translation_function(language_code)

    async with postgres_pool.acquire() as conn:
        # Use the check_active_quiz function to determine if a quiz is in progress
        active_section = await check_active_quiz(conn, chat_id)

    if active_section:
        section = active_section['section']
        index = active_section['current_index']
        logging.debug(f"User {chat_id} is in the middle of a quiz (section: {section}, index: {index})")

        # Inform the user that their progress will be saved
        await update.message.reply_text(
            _("I will save your progress, and you can continue after subscribing.")
        )
    # Proceed with asking for the email
    await update.message.reply_text(
        _("You are going to subscribe to the exclusive content via emails, including updates about future bots. "
        "Please enter your email to subscribe, or type 'Skip' to cancel.")
    )
    waiting_for_email[chat_id] = True  # Mark that we're waiting for the email
    # Store the active quiz session in context
    context.user_data['active_section'] = active_section

async def info_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.message.chat_id
    logging.debug(f"info_command called with chat_id={chat_id}")

    # Retrieve language code
    language_code = context.user_data.get('language_code')
    if not language_code:
        # Fetch from the database if not in user_data
        async with postgres_pool.acquire() as conn:
            language_code = await conn.fetchval("""
                SELECT language FROM users WHERE chat_id = $1
            """, chat_id)
            if not language_code:
                language_code = 'en'
            context.user_data['language_code'] = language_code

    _ = get_translation_function(language_code)

    async with postgres_pool.acquire() as conn:
        # Use the check_active_quiz function to determine if a quiz is in progress
        active_section = await check_active_quiz(conn, chat_id)

    if active_section:
        section = active_section['section']
        index = active_section['current_index']
        logging.debug(f"User {chat_id} is in the middle of a quiz (section: {section}, index: {index})")

        # Inform the user that their progress will be saved
        await update.message.reply_text(
            _("I will save your progress, and you can continue after viewing the info.")
        )
    
    # Construct the information message
    info_message = _(
        "🤖 *Bot Information*\n\n"
        "I am a bot designed to help you prepare for QA Engineer job interviews. "
        "My knowledge is based on real people's interview reviews that are publicly available on sites like Glassdoor and the interview experiences of the author and her colleagues.\n\n"
        "Below are some useful links and resources:\n\n"
        "[YouTube Channel](https://www.youtube.com/channel/yourchannel)\n\n"
        "Feel free to explore the resources and enhance your knowledge!"
    )

    await update.message.reply_text(info_message, parse_mode='Markdown', reply_markup=ReplyKeyboardRemove())

    # Store the active quiz session in context to resume later
    context.user_data['active_section'] = active_section
    await resume_quiz_if_applicable(update, context, chat_id)

async def send_question(update, context, chat_id, questions, section_str: str):
    # Retrieve language code
    language_code = context.user_data.get('language_code')
    if not language_code:
        # Fetch from the database if not in user_data
        async with postgres_pool.acquire() as conn:
            language_code = await conn.fetchval("""
                SELECT language FROM users WHERE chat_id = $1
            """, chat_id)
            if not language_code:
                language_code = 'en'
            context.user_data['language_code'] = language_code

    _ = get_translation_function(language_code)
    # Fetch current index from the database
    # Get a connection from the pool
    try:
        async with postgres_pool.acquire() as conn:
            index = await conn.fetchval("SELECT current_index FROM user_progress WHERE user_id = (SELECT user_id FROM users WHERE chat_id = $1) AND section = $2", chat_id, section_str)
            index = index or 0  # Default to 0 if no record found
    except Exception as e:
        logging.error(f"Error in send_question: {str(e)}")
        await update.message.reply_text(_("An error occurred. We're on it—please try again soon."))

    question_data = questions[index]
    logging.debug(f"Sending question with chat_id={chat_id} and section={section_str} and question index={index}")
    # Создаем список кнопок для вариантов ответов
    keyboard = []

    # Получаем текст для кнопок из данных вопроса
    answers = question_data['answers']

    # Разбиваем кнопки на две колонки
    col1 = [KeyboardButton(answers[0]['answer_text']), KeyboardButton(answers[2]['answer_text'])]  # Первая колонка
    col2 = [KeyboardButton(answers[1]['answer_text']), KeyboardButton(_("Skip question"))]  # Вторая колонка

    # Составляем клавиатуру из двух колонок
    for i in range(len(col1)):
        row = [col1[i], col2[i]]  # В одной строке две кнопки: из первой и второй колонок
        keyboard.append(row)

    # Создаем разметку клавиатуры
    reply_markup = ReplyKeyboardMarkup(keyboard, one_time_keyboard=True, resize_keyboard=True)

    # Send the separator
    await context.bot.send_message(
         chat_id=chat_id,
         text=f"<b>• • • 📚 📚 📚 • • • </b>",
         parse_mode='HTML',
    )
    # await asyncio.sleep(1)

    # # Удаляем сообщение
    # await context.bot.delete_message(
    #     chat_id=chat_id,
    #     message_id=sent_message.message_id
    # )
    await context.bot.send_message(
        chat_id=chat_id,
        text=f"🧩 {question_data['question_text']}",
        parse_mode='HTML',
        reply_markup=reply_markup
    )

async def handle_quiz(update, context, questions, section_str: str):
    chat_id = update.message.chat_id
    text = update.message.text
    logging.debug(f"handle_quiz called with chat_id={chat_id}, text={text}, section={section_str}")

    # Retrieve language code
    language_code = context.user_data.get('language_code')
    if not language_code:
        # Fetch from the database if not in user_data
        async with postgres_pool.acquire() as conn:
            language_code = await conn.fetchval("""
                SELECT language FROM users WHERE chat_id = $1
            """, chat_id)
            if not language_code:
                language_code = 'en'
            context.user_data['language_code'] = language_code

    _ = get_translation_function(language_code)

    # Fetch question data and index from database
    async with postgres_pool.acquire() as conn:
        try:
            index = await conn.fetchval("""
                SELECT current_index FROM user_progress 
                WHERE user_id = (SELECT user_id FROM users WHERE chat_id = $1) 
                AND section = $2
            """, chat_id, section_str)
            if index is not None:
                question_data = questions[index]
                logging.info(f"Retrieved question index {index} for chat_id={chat_id}, section={section_str}")
                # Determine if the provided answer is correct
                selected_answer = None                
                if text == _("Skip question"):
                    # Increment skipped counter
                    await conn.execute("""
                        UPDATE user_progress 
                        SET skipped_questions = skipped_questions + 1 
                        WHERE user_id = (SELECT user_id FROM users WHERE chat_id = $1) AND section = $2
                    """, chat_id, section_str)
                    new_index = index + 1 if (index + 1 < len(questions)) else 0  # Move to next question, wrap around if at the end
                    # Update the user's progress
                    await conn.execute("""
                        UPDATE user_progress 
                        SET current_index = $1 
                        WHERE user_id = (SELECT user_id FROM users WHERE chat_id = $2) AND section = $3
                    """, new_index, chat_id, section_str)
                elif text == _("No, continue my current session"):
                    # Just re-send the current question, do not increment the index
                    await send_question(update, context, chat_id, questions, section_str)
                    return
                else:
                    selected_answer = next((answer for answer in question_data['answers'] if answer['answer_text'] == text), None)
                    if selected_answer:
                        if selected_answer['is_correct']:
                            # await context.bot.send_message(
                            #     chat_id=chat_id,
                            #     text="🌟\n",  # Анимация конфетти для празднования правильного ответа
                            # )
                            sent_message=await context.bot.send_message(
                                chat_id=chat_id,
                                text="🌟\n"
                            )
                            await asyncio.sleep(0.5)

                            # Удаляем сообщение
                            await context.bot.delete_message(
                                 chat_id=chat_id,
                                 message_id=sent_message.message_id
                            )
                            response = _("🌟 Correct!\n\n{explanation}").format(explanation=selected_answer['explanation'])
                        else:
                            sent_message=await context.bot.send_message(
                                chat_id=chat_id,
                                text="❗️\n"
                            )
                            await asyncio.sleep(0.5)

                            # Удаляем сообщение
                            await context.bot.delete_message(
                                 chat_id=chat_id,
                                 message_id=sent_message.message_id
                            )
                            response = _("❗️ That's not the right answer.\n\n{explanation}").format(explanation=selected_answer['explanation'])
                        await update.message.reply_text(response)
                        # Increment correct or incorrect counter
                        field = 'correct_answers' if selected_answer['is_correct'] else 'incorrect_answers'
                        await conn.execute(f"""
                            UPDATE user_progress 
                            SET {field} = {field} + 1 
                            WHERE user_id = (SELECT user_id FROM users WHERE chat_id = $1) AND section = $2
                        """, chat_id, section_str)
                    # Calculate the new index
                    new_index = index + 1 if (index + 1 < len(questions)) else 0
                    # Update the user's progress
                    logging.info(f"Updating user_progress with new_index={new_index} for chat_id={chat_id}, section={section_str}")
                    await conn.execute("""
                        UPDATE user_progress 
                        SET current_index = $1 
                        WHERE user_id = (SELECT user_id FROM users WHERE chat_id = $2) AND section = $3
                    """, new_index, chat_id, section_str)
                # Update the user progress
                # logging.info(f"Updating user_progress with new_index={new_index} for chat_id={chat_id}, section={section_str}")
                # await conn.execute("""
                #     INSERT INTO user_progress (user_id, section, current_index)
                #     VALUES ((SELECT user_id FROM users WHERE chat_id = $1), $2, $3)
                #     ON CONFLICT (user_id, section) DO UPDATE SET current_index = EXCLUDED.current_index
                #  """, chat_id, section_str, new_index)

                if new_index > 0:
                    await send_question(update, context, chat_id, questions, section_str)
                else:
                    logging.info(f"Printing statistics of answers for chat_id={chat_id}, section={section_str}")
                    stats = await conn.fetchrow("""
                        SELECT correct_answers, incorrect_answers, skipped_questions 
                        FROM user_progress 
                        WHERE user_id = (SELECT user_id FROM users WHERE chat_id = $1) AND section = $2
                    """, chat_id, section_str)
                    # Get the button label for the section
                    button_label = label_to_section.get(section_str, section_str)
                    await context.bot.send_message(
                        chat_id=chat_id,
                        text=f"<b>• • • ✔️ ✔️ ✔️ • • • </b>",
                        parse_mode='HTML',
                    )
                    stats_message = _(
                        "Good job! You've completed all the questions in the {section} section with the following results:\n"
                        "Correct: {correct}\n"
                        "Incorrect: {incorrect}\n"
                        "Skipped: {skipped}"
                    ).format(
                    section=button_label,
                    correct=stats['correct_answers'],
                    incorrect=stats['incorrect_answers'],
                    skipped=stats['skipped_questions']
                    )

                    await update.message.reply_text(stats_message)

                    logging.info(f"Resetting user_progress with new_index=NULL for chat_id={chat_id}, section={section_str}")
                    await conn.execute("""
                        UPDATE user_progress 
                        SET correct_answers = 0, incorrect_answers = 0, skipped_questions = 0, current_index = NULL
                        WHERE user_id = (SELECT user_id FROM users WHERE chat_id = $1) AND section = $2
                    """, chat_id, section_str)  # Note the parameters are not in a single tuple here.

                    # Send completion message with keyboard for choosing another section
                    keyboard = [[KeyboardButton(button_labels[s])] for s in Section]
                    reply_markup = ReplyKeyboardMarkup(keyboard, one_time_keyboard=True, resize_keyboard=True)
                    completion_message = _("Ready for more? Choose another section to keep practicing, or redo this one for perfection!")
                    await update.message.reply_text(completion_message, reply_markup=reply_markup)
            else:
                logging.warning(f"No progress found for chat_id={chat_id}, section={section_str}")
                await update.message.reply_text(_("It looks like you don't have any active quizzes."))
        except Exception as e:
            logging.error(f"Database error in handle_quiz for chat_id={chat_id}: {e}")
            await update.message.reply_text(_("A database error occurred. Please try again later."))

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.message.chat_id
    text = update.message.text
    logging.debug(f"Received message: {text} from chat_id: {chat_id}")

    # Retrieve language code
    language_code = context.user_data.get('language_code')
    if not language_code:
        # Fetch from the database if not in user_data
        async with postgres_pool.acquire() as conn:
            language_code = await conn.fetchval("""
                SELECT language FROM users WHERE chat_id = $1
            """, chat_id)
            if not language_code:
                language_code = 'en'
            context.user_data['language_code'] = language_code

    _ = get_translation_function(language_code)

    if chat_id in waiting_for_email and waiting_for_email[chat_id]:
        # Handle email or skipping logic
        if text.lower() == "skip":
            logging.debug(f"User {chat_id} chose to skip subscribing.")
            waiting_for_email[chat_id] = False
            await update.message.reply_text(
                _("No problem! You can subscribe anytime by using the /subscribe command."),
                reply_markup=ReplyKeyboardRemove()
            )
            await resume_quiz_if_applicable(update, context, chat_id)
        elif "@" in text and "." in text:
            async with postgres_pool.acquire() as conn:
                user_id = await conn.fetchval("""
                    SELECT user_id FROM users WHERE chat_id = $1
                """, chat_id)
                await conn.execute("""
                    INSERT INTO user_details (user_id, email, subscribed) VALUES ($1, $2, TRUE)
                    ON CONFLICT (user_id) DO UPDATE SET email = EXCLUDED.email, subscribed = TRUE
                """, user_id, text)
            logging.debug(f"Email {text} stored for user {chat_id} with subscription.")
            waiting_for_email[chat_id] = False
            await update.message.reply_text(
                _("Thank you for subscribing!"),
                reply_markup=ReplyKeyboardRemove()
            )
            await resume_quiz_if_applicable(update, context, chat_id)
        else:
            logging.debug(f"User {chat_id} provided an invalid email: {text}")
            await update.message.reply_text(
                _("That doesn't seem like a valid email. Please enter a valid email address or type 'Skip' to cancel.")
            )
        return

    # Update last active date
    async with postgres_pool.acquire() as conn:
        await conn.execute("""
            UPDATE user_details 
            SET last_active_date = NOW() 
            WHERE user_id = (SELECT user_id FROM users WHERE chat_id = $1)
        """, chat_id)

    if text in ["English", "Русский"]:
        language = 'ru' if text == "Русский" else 'en'
        logging.info(f"Updating user language for chat_id={chat_id}, with language={language}")
        async with postgres_pool.acquire() as conn:
            await conn.execute("UPDATE users SET language = $1 WHERE chat_id = $2", language, chat_id)
        context.user_data['language_code'] = language
        _ = get_translation_function(language)
        await update.message.reply_text(_("Language updated."))
        await resume_quiz_if_applicable(update, context, chat_id)  # Resume quiz if there was one
        return

    # Handle the responses to the start command reset prompt
    if text == _("Yes, reset progress"):
        async with postgres_pool.acquire() as conn:
            await reset_and_start_new_session(conn, chat_id, update, context)
        return
    elif text == _("No, continue where I left off"):
        # Handle continuation without resetting progress
        await update.message.reply_text(_("Great, let's pick up where you left off..."))
        try:
            async with postgres_pool.acquire() as conn:
                active_section = await check_active_quiz(conn, chat_id)
                if active_section:
                    await resume_quiz_if_applicable(update, context, chat_id)
                else:
                    await update.message.reply_text(_("No active session found. Please start a new one."))
        except Exception as e:
            logging.error(f"Error handling continuation: {e}")
            await update.message.reply_text(_("There was an issue processing your request. Please try again later."))
        return

    # Mapping the text from the keyboard to the section command
    section_map = {label: section.value for section, label in button_labels.items()}
    if text in section_map:
        # Call the correct function based on the keyboard input
        await section_command(update, context, section_map[text])
        return
    
    # Initialize active_section outside of the connection scope
    active_section = None
    # Handle quiz-related interactions or other messages
    try:
        async with postgres_pool.acquire() as conn:
            active_section = await conn.fetchrow("""
                SELECT section, current_index FROM user_progress 
                WHERE user_id = (SELECT user_id FROM users WHERE chat_id = $1)
                AND current_index IS NOT NULL
            """, chat_id)

            if active_section:
                section, index = active_section['section'], active_section['current_index']
                logging.debug(f"section for active_section {section} for chat_id={chat_id}, index={index}")
                if index is not None:
                    # TO DO Implement caching
                    # Retrieve language_code from context.user_data
                    language_code = context.user_data.get('language_code', 'en')
                    # Fetch questions with the language_code
                    questions = await fetch_questions(conn, section, language_code)
                    if questions and index < len(questions):
                        logging.debug(f"Active Section: {section}")
                        await handle_quiz(update, context, questions, section)
                    else:
                        await update.message.reply_text(_("You've completed all questions in this section. Choose another section!"))
                return
            else:
                if 'hello' in text.lower():
                    await update.message.reply_text(_("Hello! Click on /start menu ;)"))
                elif 'help' in text.lower():
                    await update.message.reply_text(_("If you need help, please send an email to irina.sokolova.qa@gmail.com with a detailed description of your issue. Type '/start' to work with the bot."))
                else:
                    await update.message.reply_text(_("I'm not sure how to respond to that. Please type '/start' or 'help'."))
    except Exception as e:
        logging.error(f"Error handling message: {e}")
        await update.message.reply_text(_("There was an issue processing your request. Please try again later."))

async def check_active_quiz(conn, chat_id):
    """
    Check if there is an active quiz session for the user.
    :param conn: The database connection object.
    :param chat_id: The chat ID of the user.
    :return: A dictionary with section and current_index if an active session exists, otherwise None.
    """
    active_section = await conn.fetchrow("""
        SELECT section, current_index FROM user_progress 
        WHERE user_id = (SELECT user_id FROM users WHERE chat_id = $1)
        AND current_index IS NOT NULL
    """, chat_id)
    return active_section

async def resume_quiz_if_applicable(update: Update, context: ContextTypes.DEFAULT_TYPE, chat_id):
    # Retrieve language code
    language_code = context.user_data.get('language_code')
    if not language_code:
        # Fetch from the database if not in user_data
        async with postgres_pool.acquire() as conn:
            language_code = await conn.fetchval("""
                SELECT language FROM users WHERE chat_id = $1
            """, chat_id)
            if not language_code:
                language_code = 'en'
            context.user_data['language_code'] = language_code

    _ = get_translation_function(language_code)
    # Check if there's a quiz to resume using context or directly via the function
    active_section = context.user_data.get('active_section')
    if not active_section:
        async with postgres_pool.acquire() as conn:
            active_section = await check_active_quiz(conn, chat_id)
            if not active_section:
                # Automatically trigger the /start command
                await start_command(update, context)
                return
    if active_section:
        logging.debug(f"Resuming quiz for chat_id={chat_id}, section={active_section['section']}, index={active_section['current_index']}")
        # Fetch the questions again based on the saved section
        async with postgres_pool.acquire() as conn:
            language_code = context.user_data.get('language_code', 'en')
            questions = await fetch_questions(conn, active_section['section'], language_code)
            if questions and active_section['current_index'] < len(questions):
                await send_question(update, context, chat_id, questions, active_section['section'])
            else:
                await update.message.reply_text(_("We had trouble fetching the questions. Please start over."))
        # Clear the saved state after resumption
        context.user_data.pop('active_section', None)
    else:
        logging.debug(f"No active quiz session to resume for chat_id={chat_id}.")

async def error(update, context):
    print(f'Update {update} caused error {context.error}')
    traceback.print_exception(None, context.error, context.error.__traceback__)

async def set_webhook(app):
    await app.bot.set_webhook(WEBHOOK_URL)
    print(f"Webhook set to {WEBHOOK_URL}")  # Adding a print statement to confirm the URL.

# Registering commands and message handlers
if __name__ == '__main__':
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:  # 'RuntimeError: no running event loop'
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
    postgres_pool = loop.run_until_complete(create_pool())
    try:
        app = Application.builder().token(TOKEN).build()
        # Set up the webhook
        # asyncio.run(set_webhook(app))
        loop.run_until_complete(set_webhook(app))
        # postgres_pool = asyncio.get_event_loop().run_until_complete(create_pool())
        app.add_handler(CommandHandler('start', start_command))
        app.add_handler(CommandHandler('language', set_language_command))
        app.add_handler(CommandHandler('subscribe', subscribe_command))
        app.add_handler(CommandHandler('info', info_command))
        app.add_handler(MessageHandler(filters.TEXT, handle_message))
        app.add_error_handler(error)
        # Start the server with webhook configuration
        print('Starting the application with webhook configuration...')
        app.run_webhook(listen="0.0.0.0",
                        port=int(os.environ.get('PORT', '8443')),
                        url_path='webhook'+'/'+secure_path,  # Use the secure path
                        webhook_url=WEBHOOK_URL)
    except Exception as e:
        logging.error(f"Error starting the application: {e}")