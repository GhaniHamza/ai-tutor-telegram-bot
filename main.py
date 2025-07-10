import logging
import json # Make sure this is at the top
import base64 # And this one too
import os # And this one
from functools import wraps
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, ReplyKeyboardRemove
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    filters,
    ContextTypes,
    ConversationHandler,
    CallbackQueryHandler,
)
import firebase_admin
from firebase_admin import credentials, firestore
import google.generativeai as genai

# --- SETUP ---
TOKEN = os.environ.get("TELEGRAM_TOKEN")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
AI_MODEL_NAME = "models/gemini-1.5-flash-latest"
# --- END SETUP ---

# --- KEYBOARDS ---
MAIN_MENU_KEYBOARD = [["🧑‍🏫 Tutor", "❓ Quiz Me"], ["📚 My Subjects", "➕ Add Subject"]]
MAIN_MENU_MARKUP = ReplyKeyboardMarkup(MAIN_MENU_KEYBOARD, resize_keyboard=True)

# --- BASIC SETUP ---
logging.basicConfig(format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO)
logger = logging.getLogger(__name__)
db, ai_model = None, None

# --- FIREBASE INITIALIZATION ---
try:
    # These lines MUST be indented
    base64_creds = os.environ.get("FIREBASE_CREDENTIALS_BASE64")
    if base64_creds:
        json_creds_str = base64.b64decode(base64_creds).decode('utf-8')
        firebase_credentials_dict = json.loads(json_creds_str)
        cred = credentials.Certificate(firebase_credentials_dict)
        if not firebase_admin._apps:
            firebase_admin.initialize_app(cred)
        db = firestore.client()
        logger.info("Firebase connected successfully via Base64 credentials!")
    else:
        logger.error("FATAL: FIREBASE_CREDENTIALS_BASE64 environment variable not found.")
except Exception as e:
    # This line MUST be indented
    logger.error(f"FATAL: DB connection failed from Base64 credentials: {e}")

# --- AI INITIALIZATION ---
try:
    # These lines MUST be indented
    if GEMINI_API_KEY:
        genai.configure(api_key=GEMINI_API_KEY)
        ai_model = genai.GenerativeModel(AI_MODEL_NAME)
        logger.info(f"Google AI configured successfully with model: {AI_MODEL_NAME}")
    else:
        logger.error("FATAL: GEMINI_API_KEY environment variable not found.")
except Exception as e:
    # This line MUST be indented
    logger.error(f"FATAL: AI config failed: {e}")

# --- GLOBAL DEFS ---
AVAILABLE_SUBJECTS = ["ICT", "English", "Math", "Physics"]
GET_EMAIL_REG = range(1)
CHECK_EMAIL_LOGIN = range(1)
T_SELECT_SUBJECT, T_ASK_QUESTION, T_TUTORING = range(1, 4)

# --- LOGIN REQUIRED DECORATOR ---
def login_required(func):
    @wraps(func)
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE, *args, **kwargs):
        if context.user_data.get('is_logged_in'):
            return await func(update, context, *args, **kwargs)
        else:
            await update.message.reply_text("🔒 This feature requires you to be logged in. Please use /login.", reply_markup=ReplyKeyboardRemove())
            return ConversationHandler.END
    return wrapper

# --- NEW IGCSE-FOCUSED QUIZ FUNCTION ---
async def generate_quiz_from_ai(subject_name: str):
    """Creates a prompt and generates a quiz specifically for the IGCSE syllabus."""
    if not ai_model:
        return "Sorry, the AI service is currently unavailable."
    
    prompt = f"""
    You are an expert IGCSE exam creator. Your single task is to create a quiz.

    Instructions:
    1. Create a 5-question multiple-choice quiz about the subject: "{subject_name}".
    2. **CRITICAL**: The questions, terminology, and concepts must strictly adhere to the IGCSE syllabus. Do not include content from A-Levels, AP, or other curricula.
    3. For each question, provide 4 options (A, B, C, D).
    4. After all 5 questions, create a separate section titled "🔑 Answer Key".
    5. In the answer key, list the correct answer and a brief, one-sentence explanation that is relevant to the IGCSE context.
    """
    try:
        response = await ai_model.generate_content_async(prompt)
        return response.text
    except Exception as e:
        logger.error(f"AI quiz generation failed: {e}")
        return "Sorry, an error occurred while creating your quiz. Please try again later."

# --- CORE COMMANDS (START, LOGIN, LOGOUT, REGISTER) ---
# ... This code remains unchanged from the previous version ...
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.message.from_user.id)
    if context.user_data.get('is_logged_in'):
        await update.message.reply_text("Welcome back! What would you like to do?", reply_markup=MAIN_MENU_MARKUP)
        return
    user_doc = db.collection('users').document(user_id).get() if db else None
    if user_doc and user_doc.exists:
        await update.message.reply_text("Welcome back! Please use /login with your email to access your account.", reply_markup=ReplyKeyboardRemove())
    else:
        await update.message.reply_text("Welcome! Please use /register to create a new account.", reply_markup=ReplyKeyboardRemove())

async def logout_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    await update.message.reply_text("You have been successfully logged out. Use /login to access your account again.", reply_markup=ReplyKeyboardRemove())

# --- Registration Conversation ---
async def start_registration(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.message.reply_text("Let's create your account. Please enter your email address.", reply_markup=ReplyKeyboardRemove())
    return GET_EMAIL_REG

async def get_email_and_register(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user, email = update.message.from_user, update.message.text.lower()
    docs = db.collection('users').where('email', '==', email).limit(1).stream() if db else []
    if len(list(docs)) > 0:
        await update.message.reply_text("This email is already registered. Please /login or use a different email.")
        return ConversationHandler.END
    try:
        db.collection('users').document(str(user.id)).set({'email': email, 'telegram_username': user.username or "N/A", 'subjects': []})
        await update.message.reply_text("✅ Registration complete! You can now use /login with your email.")
    except Exception as e:
        logger.error(f"Error saving user {user.id}: {e}")
        await update.message.reply_text("Couldn't save your data. Please try again.")
    return ConversationHandler.END

# --- Login Conversation ---
async def start_login(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if context.user_data.get('is_logged_in'):
        await update.message.reply_text("You are already logged in.", reply_markup=MAIN_MENU_MARKUP)
        return ConversationHandler.END
    await update.message.reply_text("To log in, please enter your registered email address:", reply_markup=ReplyKeyboardRemove())
    return CHECK_EMAIL_LOGIN

async def check_email_and_login(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user_email_input, user_id = update.message.text.lower(), str(update.message.from_user.id)
    user_doc = db.collection('users').document(user_id).get() if db else None
    if user_doc and user_doc.exists and user_email_input == user_doc.to_dict().get('email'):
        context.user_data['is_logged_in'] = True
        await update.message.reply_text("✅ Login successful! Welcome.", reply_markup=MAIN_MENU_MARKUP)
    else:
        await update.message.reply_text("❌ Incorrect email or user not registered. Please try again or use /register.")
    return ConversationHandler.END

async def cancel_conversation(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if context.user_data.get('is_logged_in'):
        await update.message.reply_text("Action canceled. Returning to the main menu.", reply_markup=MAIN_MENU_MARKUP)
    else:
        await update.message.reply_text("Action canceled.")
    return ConversationHandler.END

# --- PROTECTED FEATURES ---

def get_user_subjects(user_id):
    if not db: return []
    try:
        user_doc = db.collection('users').document(str(user_id)).get()
        if user_doc.exists: return user_doc.to_dict().get('subjects', [])
    except Exception as e: logger.error(f"Error fetching subjects for {user_id}: {e}")
    return []

@login_required
async def add_subject_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    current_subjects = get_user_subjects(update.message.from_user.id)
    keyboard = [[InlineKeyboardButton(s, callback_data=f"add_{s}")] for s in AVAILABLE_SUBJECTS if s not in current_subjects]
    if not keyboard: await update.message.reply_text("You've added all available subjects!", reply_markup=MAIN_MENU_MARKUP)
    else: await update.message.reply_text("Choose a subject to add:", reply_markup=InlineKeyboardMarkup(keyboard))

@login_required
async def my_subjects_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    subjects = get_user_subjects(update.message.from_user.id)
    if not subjects: await update.message.reply_text("You have no subjects yet.", reply_markup=MAIN_MENU_MARKUP)
    else:
        keyboard = [[InlineKeyboardButton(s, callback_data="n_"), InlineKeyboardButton("❌ Remove", callback_data=f"remove_{s}")] for s in subjects]
        await update.message.reply_text("Your subjects:", reply_markup=InlineKeyboardMarkup(keyboard))

# --- NEW: /quizme Command ---
@login_required
async def quiz_me_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Starts the quiz flow by asking for a subject."""
    subjects = get_user_subjects(update.message.from_user.id)
    if not subjects:
        await update.message.reply_text("You need to add a subject first! Use the '➕ Add Subject' button.", reply_markup=MAIN_MENU_MARKUP)
        return
    # Create buttons with a 'quiz_' prefix to identify the action
    keyboard = [[InlineKeyboardButton(s, callback_data=f"quiz_{s}")] for s in subjects]
    await update.message.reply_text("Which subject would you like a quiz on?", reply_markup=InlineKeyboardMarkup(keyboard))


# --- Tutor Mode (Unchanged) ---
@login_required
async def start_tutor_session(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    # ... Same as before ...
    subjects = get_user_subjects(update.message.from_user.id)
    if not subjects:
        await update.message.reply_text("Add a subject first with the '➕ Add Subject' button.", reply_markup=MAIN_MENU_MARKUP)
        return ConversationHandler.END
    keyboard = [[InlineKeyboardButton(s, callback_data=f"tutor_{s}")] for s in subjects]
    await update.message.reply_text("Which subject for tutoring?", reply_markup=InlineKeyboardMarkup(keyboard))
    return T_SELECT_SUBJECT

# --- NEW IGCSE-FOCUSED TUTOR INITIATION ---
async def start_ai_conversation(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Initializes the AI chat session with a strict IGCSE Tutor persona."""
    initial_question = update.message.text
    subject = context.user_data.get('tutor_subject', 'this subject')
    await update.message.reply_text("Initializing IGCSE Tutor... Please wait.")

    try:
        # This history block creates the AI's persona and rules.
        chat_history = [
            {'role': 'user', 'parts': [f"""
            Your identity: You are a highly specialized AI Tutor for the IGCSE syllabus. 
            Your ONLY focus is the IGCSE curriculum for the subject: {subject}.
            
            Your rules:
            1. All your explanations, examples, and answers MUST be strictly relevant to the IGCSE syllabus.
            2. If a student asks a question outside this scope, gently guide them back by saying something like, "That's an interesting question, but for the IGCSE syllabus, we should focus on..."
            3. Use terminology and examples that are common in IGCSE textbooks and exams.
            4. Be patient, encouraging, and clear.
            
            Start our conversation by introducing yourself as their personal IGCSE tutor for {subject}.
            """]},
            # We pre-fill the model's first response to ensure it understands its role.
            {'role': 'model', 'parts': [f"Hello! I am your personal IGCSE Tutor for {subject}. I'm ready to help you with any questions you have about the syllabus. What topic can I help you understand today?"]}
        ]

        # Start the chat with this pre-defined history
        chat_session = ai_model.start_chat(history=chat_history)
        
        # Now, send the user's *actual* first question to this pre-initialized session
        response = await chat_session.send_message_async(initial_question)
        context.user_data['chat_session'] = chat_session
        
        await update.message.reply_text(response.text, reply_markup=ReplyKeyboardRemove())
        await update.message.reply_text("👆 You are now chatting with the IGCSE Tutor. Type /done to end the session.")
        return T_TUTORING
    except Exception as e:
        logger.error(f"AI chat initiation failed: {e}")
        await update.message.reply_text("Couldn't connect to the AI Tutor. Session ended.", reply_markup=MAIN_MENU_MARKUP)
        return ConversationHandler.END

# --- UNIVERSAL INLINE BUTTON HANDLER ---
async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    action, data = query.data.split('_', 1)
    user_id = str(query.from_user.id)

    if action == "add":
        db.collection('users').document(user_id).update({'subjects': firestore.ArrayUnion([data])})
        await query.edit_message_text(text=f"✅ Added '{data}'!")
    elif action == "remove":
        db.collection('users').document(user_id).update({'subjects': firestore.ArrayRemove([data])})
        new_subjects = get_user_subjects(user_id)
        if not new_subjects: await query.edit_message_text(text=f"✅ Removed '{data}'. You have no subjects left.")
        else:
            keyboard = [[InlineKeyboardButton(s, callback_data="n_"), InlineKeyboardButton("❌ Remove", callback_data=f"remove_{s}")] for s in new_subjects]
            await query.edit_message_text(text=f"✅ Removed '{data}'. Your updated list:", reply_markup=InlineKeyboardMarkup(keyboard))
    elif action == "tutor":
        context.user_data['tutor_subject'] = data
        await query.edit_message_text(text=f"Great! Ask your question about {data}:")
        return T_ASK_QUESTION
    elif action == "quiz":
        await query.edit_message_text(text=f"⏳ Generating a quiz for '{data}'... Please wait.")
        quiz_text = await generate_quiz_from_ai(data)
        await query.message.reply_text(text=quiz_text, reply_markup=MAIN_MENU_MARKUP)
        # We can also edit the original message to confirm completion
        await query.edit_message_text(text=f"✅ Your quiz for '{data}' is ready!")

    if 'tutor_subject' not in context.user_data: return ConversationHandler.END
    else: return T_ASK_QUESTION

# --- MAIN ---
def main():
    application = Application.builder().token(TOKEN).build()
    
    # Conversation Handlers
    reg_handler = ConversationHandler(entry_points=[CommandHandler("register", start_registration)], states={GET_EMAIL_REG: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_email_and_register)]}, fallbacks=[CommandHandler("cancel", cancel_conversation)])
    login_handler = ConversationHandler(entry_points=[CommandHandler("login", start_login)], states={CHECK_EMAIL_LOGIN: [MessageHandler(filters.TEXT & ~filters.COMMAND, check_email_and_login)]}, fallbacks=[CommandHandler("cancel", cancel_conversation)])
    tutor_handler = ConversationHandler(
        entry_points=[CommandHandler("tutor", start_tutor_session), MessageHandler(filters.Regex("^🧑‍🏫 Tutor$"), start_tutor_session)],
        states={
            T_SELECT_SUBJECT: [CallbackQueryHandler(button_handler, pattern="^tutor_")],
            T_ASK_QUESTION: [MessageHandler(filters.TEXT & ~filters.COMMAND, start_ai_conversation)],
            T_TUTORING: [MessageHandler(filters.TEXT & ~filters.COMMAND, forward_to_ai)]},
        fallbacks=[CommandHandler("done", end_tutor_session)]
    )

    # Add all handlers
    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("logout", logout_command))
    application.add_handler(reg_handler)
    application.add_handler(login_handler)
    application.add_handler(tutor_handler)
    # Protected commands
    application.add_handler(CommandHandler("mysubjects", my_subjects_command))
    application.add_handler(CommandHandler("addsubject", add_subject_command))
    application.add_handler(CommandHandler("quizme", quiz_me_command))
    # Handlers for menu buttons
    application.add_handler(MessageHandler(filters.Regex("^📚 My Subjects$"), my_subjects_command))
    application.add_handler(MessageHandler(filters.Regex("^➕ Add Subject$"), add_subject_command))
    application.add_handler(MessageHandler(filters.Regex("^❓ Quiz Me$"), quiz_me_command))
    # Universal handler for inline buttons (add, remove, quiz)
    application.add_handler(CallbackQueryHandler(button_handler, pattern="^(add_|remove_|quiz_)"))

    print("Bot is running...")
    application.run_polling()

if __name__ == "__main__":
    main()