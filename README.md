# Telegram Bot Python

This codebase was initiated using the tutorial available at [this YouTube link](https://www.youtube.com/watch?v=vZtm1wuA2yc) as a starting point.

## General Workflow:
1. User initiates the bot with `/start`.
2. The bot registers the user and presents different section options.
3. User selects a preferred section.
4. The bot sends a question from the chosen section.
5. User submits their answer.
6. The bot validates the answer, provides feedback, and either sends the next question or a completion message depending on the progress.
7. The bot handles all other user messages and errors appropriately.

## Deployment on Heroku
The bot is deployed on Heroku. Follow these commands to manage deployment:

```bash
heroku login
cd path/to/your/project
git init
git add .
git commit -am "Initial commit"
heroku git:remote -a telegram-bot-python
git push heroku master
heroku logs --tail
```
TO DO on HEROKU:
ALTER TABLE users ADD COLUMN language VARCHAR(10) DEFAULT 'en';

CREATE TABLE user_details (
    user_id INT PRIMARY KEY REFERENCES users(user_id) ON DELETE CASCADE,
    first_name VARCHAR(255),
    last_name VARCHAR(255),
    username VARCHAR(255),
    language_code VARCHAR(10),
    join_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    last_active_date TIMESTAMP,
    email VARCHAR(255)
);

ALTER TABLE user_details ADD COLUMN subscribed BOOLEAN DEFAULT FALSE;

---
to get <constraint_name>:
SELECT conname
FROM pg_constraint
WHERE conrelid = 'answers'::regclass AND confrelid = 'questions'::regclass;

ALTER TABLE answers DROP CONSTRAINT <constraint_name>;

ALTER TABLE answers ADD CONSTRAINT answers_question_id_fkey FOREIGN KEY (question_id) REFERENCES questions (id) ON DELETE CASCADE;

TRUNCATE TABLE answers, questions RESTART IDENTITY CASCADE;

ALTER TABLE questions
ADD COLUMN text_ru TEXT;

ALTER TABLE answers
ADD COLUMN text_ru TEXT, 
ADD COLUMN explanation_ru TEXT;



# Useful Heroku Commands:

- Restart PostgreSQL service: `heroku pg:restart --app your-app-name`
- Scale up the worker: `heroku ps:scale worker=1 -a telegram-bot-python`
- Scale down the web (stop the service): `heroku ps:scale web=0 -a telegram-bot-python`
- View logs: `heroku logs --tail -a telegram-bot-python`
- Check dynos: `heroku ps`
- Restart application (if Procfile works): `heroku restart`

**Procfile contents**:: web: python main.py (I changed worker to web to use webhooks)

**Heroku Config Vars:**

- `DATABASE_URL`
- `TOKEN`

## PostgreSQL Commands

### Locally on Laptop:

```bash
psql -U postgres
\password
\c mytestdb
select * from user_progress;
```
### Heroku PostgreSQL Database:
```bash
heroku pg:psql --app telegram-bot-python
\dt  # Lists all tables
\q   # Exit
```
## Additional Tips

- **Manage Schema Changes**: As your application evolves, alter your database schema to add new tables or modify existing ones. This can be done manually or through a database migration tool.
- **Secure Your Database**: Secure your database by managing access settings on Heroku and using strong passwords for database users.

## Migrating Questions to PostgreSQL

Initially, questions and answers were stored in Python dictionaries but have now been migrated to a PostgreSQL database.

### Schema Definition
```bash
CREATE TABLE questions (
    id SERIAL PRIMARY KEY,
    type TEXT NOT NULL,
    section TEXT NOT NULL,
    text TEXT NOT NULL
);

CREATE TABLE answers (
    id SERIAL PRIMARY KEY,
    question_id INTEGER REFERENCES questions(id),
    text TEXT NOT NULL,
    is_correct BOOLEAN NOT NULL,
    explanation TEXT
);

CREATE TYPE question_type AS ENUM ('Technical', 'Situation', 'Tool', 'Process');
ALTER TABLE questions
ALTER COLUMN type TYPE question_type USING type::question_type;

SELECT conname
FROM pg_constraint
WHERE conrelid = 'answers'::regclass AND confrelid = 'questions'::regclass;

ALTER TABLE answers DROP CONSTRAINT <constraint_name>;

ALTER TABLE answers ADD CONSTRAINT answers_question_id_fkey FOREIGN KEY (question_id) REFERENCES questions (id) ON DELETE CASCADE;

ALTER TABLE questions
ADD COLUMN text_ru TEXT;

ALTER TABLE answers
ADD COLUMN text_ru TEXT, 
ADD COLUMN explanation_ru TEXT;

```
```bash
CREATE TABLE users (
 user_id SERIAL PRIMARY KEY,
 chat_id BIGINT UNIQUE NOT NULL
);

ALTER TABLE users ADD COLUMN language VARCHAR(10) DEFAULT 'en';

CREATE TABLE user_progress (
 user_id INT,
 section VARCHAR(50),
 current_index INT,
 CONSTRAINT fk_user
 FOREIGN KEY(user_id) 
 REFERENCES users(user_id)
 ON DELETE CASCADE
);

ALTER TABLE user_progress
ADD COLUMN correct_answers INT DEFAULT 0,
ADD COLUMN incorrect_answers INT DEFAULT 0,
ADD COLUMN skipped_questions INT DEFAULT 0;

CREATE TABLE user_details (
    user_id INT PRIMARY KEY REFERENCES users(user_id) ON DELETE CASCADE,
    first_name VARCHAR(255),
    last_name VARCHAR(255),
    username VARCHAR(255),
    language_code VARCHAR(10),
    join_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    last_active_date TIMESTAMP,
    email VARCHAR(255)
);

ALTER TABLE user_details ADD COLUMN subscribed BOOLEAN DEFAULT FALSE;

```
### Importing Data from CSV Files
```bash
COPY questions(type, section, text, text_ru) FROM 'D:/GProjects/telegram-bot-python/Docs/questions_free.csv' WITH (FORMAT csv, HEADER true);
COPY answers(question_id, text, text_ru, is_correct, explanation, explanation_ru) FROM 'D:/GProjects/telegram-bot-python/Docs/answers_free_cleaned.csv' WITH (FORMAT csv, HEADER true);
```

### Inserting data in Heroku
```bash
INSERT INTO questions (type, section, text) VALUES 
('Process', 'QC', 'What is testing?');
```
```bash
INSERT INTO answers (question_id, text, is_correct, explanation) VALUES 
(1, 'Process', TRUE, 'Testing is a process involving the execution of a system or application to identify bugs.'),
(1, 'Method', FALSE, 'The term ''method'' typically refers to a particular way of doing something. In testing, the focus is on the process rather than any specific method. Testing is a process involving the execution of a system or application to identify bugs.'),
(1, 'Object', FALSE, 'In this context, ''object'' does not apply. Testing is about the process, not an individual object or item. Testing is a process involving the execution of a system or application to identify bugs.');
```

Using **transactions** is crucial when you need to ensure that multiple related operations, such as inserting a question and its corresponding answers, either all succeed together or fail together without leaving the database in an inconsistent state.

**SQL Example Using Transactions**
Here’s how you might structure a transaction in SQL:

```bash
BEGIN; -- Start transaction

INSERT INTO questions (type, section, text) VALUES ('Process', 'QC', 'What is testing?');

-- Suppose the RETURNING clause gives us the question_id of the newly inserted question
INSERT INTO answers (question_id, text, is_correct, explanation) VALUES 
(1, 'Process', TRUE, 'Testing is a process...'),
(1, 'Method', FALSE, 'The term "method" typically refers to...'),
(1, 'Object', FALSE, 'In this context, "object" does not apply...');

COMMIT; -- Commit the transaction
If any of the INSERT statements fail, you can issue a ROLLBACK command to undo all changes made during the transaction.
```
### The possible script to add new questions to database:
```bash
import psycopg2

def add_question(question_type, section, text, answers):
    conn = psycopg2.connect("dbname=test user=postgres")
    cur = conn.cursor()
    cur.execute("INSERT INTO questions (type, section, text) VALUES (%s, %s, %s) RETURNING id", (question_type, section, text))
    question_id = cur.fetchone()[0]
    for answer in answers:
        cur.execute("INSERT INTO answers (question_id, text, is_correct, explanation) VALUES (%s, %s, %s, %s)", (question_id, answer['text'], answer['is_correct'], answer['explanation']))
    conn.commit()
    cur.close()
    conn.close()

add_question('Technical', 'IT', 'What is an API?', [
    {'text': 'Application Programming Interface', 'is_correct': True, 'explanation': 'An API is a set of routines, protocols, and tools for building software applications.'},
    {'text': 'Advanced Programming Interface', 'is_correct': False, 'explanation': 'This is incorrect. API stands for Application Programming Interface.'}
])
```
# Images
https://chatgpt.com/c/a24d15da-7779-4ea2-80c6-213e9a313be5
ALTER TABLE questions
ADD COLUMN image_url TEXT;
etc.

# BotFather
/setcommands is used for settings commands in Menu:
start - Start the bot
language - Set language / Установить язык
subscribe - Subscribe to updates
info - About and News


# Database connection pool setup
DATABASE_URL = os.environ['DATABASE_URL']
db_pool = pool.SimpleConnectionPool(1, 10, DATABASE_URL)

def get_user_state(chat_id):
    conn = db_pool.getconn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT section, question_index FROM user_state WHERE chat_id = %s", (chat_id,))
            result = cur.fetchone()
            return {'section': result[0], 'question_index': result[1]} if result else None
    finally:
        db_pool.putconn(conn)
This example ensures that connections are properly managed and returned to the pool, helping to prevent issues under load. Consider applying similar patterns throughout your code where database interactions occur.
