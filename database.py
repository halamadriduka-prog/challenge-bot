import asyncpg
from datetime import date, timedelta

class Database:
    def __init__(self, dsn: str):
        self.dsn = dsn
        self.pool = None

    async def connect(self):
        self.pool = await asyncpg.create_pool(dsn=self.dsn, min_size=1, max_size=5)
        await self.init_db()

    async def close(self):
        if self.pool:
            await self.pool.close()

    async def init_db(self):
        async with self.pool.acquire() as conn:
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS users (
                    user_id BIGINT PRIMARY KEY,
                    username TEXT,
                    full_name TEXT,
                    streak INT DEFAULT 0,
                    max_streak INT DEFAULT 0,
                    last_active_date DATE,
                    medals TEXT[] DEFAULT '{}',
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
            """)
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS tasks (
                    id SERIAL PRIMARY KEY,
                    user_id BIGINT REFERENCES users(user_id) ON DELETE CASCADE,
                    title TEXT NOT NULL,
                    target_amount TEXT,
                    is_active BOOLEAN DEFAULT TRUE,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
            """)
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS daily_logs (
                    id SERIAL PRIMARY KEY,
                    user_id BIGINT REFERENCES users(user_id) ON DELETE CASCADE,
                    log_date DATE NOT NULL,
                    task_title TEXT,
                    done_amount TEXT,
                    note TEXT,
                    mood TEXT,
                    photo_file_id TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
            """)

    async def register_user(self, user_id: int, username: str, full_name: str):
        async with self.pool.acquire() as conn:
            await conn.execute("""
                INSERT INTO users (user_id, username, full_name)
                VALUES ($1, $2, $3)
                ON CONFLICT (user_id) DO UPDATE 
                SET username = EXCLUDED.username, full_name = EXCLUDED.full_name;
            """, user_id, username, full_name)

    async def get_all_users(self):
        async with self.pool.acquire() as conn:
            return await conn.fetch("SELECT user_id, full_name, streak FROM users;")

    async def get_user_stats(self, user_id: int):
        async with self.pool.acquire() as conn:
            return await conn.fetchrow("SELECT * FROM users WHERE user_id = $1;", user_id)

    async def add_task(self, user_id: int, title: str, target_amount: str):
        async with self.pool.acquire() as conn:
            await conn.execute("""
                INSERT INTO tasks (user_id, title, target_amount)
                VALUES ($1, $2, $3);
            """, user_id, title, target_amount)

    async def get_active_tasks(self, user_id: int):
        async with self.pool.acquire() as conn:
            return await conn.fetch("""
                SELECT id, title, target_amount FROM tasks 
                WHERE user_id = $1 AND is_active = TRUE ORDER BY id ASC;
            """, user_id)

    async def delete_task(self, task_id: int, user_id: int):
        async with self.pool.acquire() as conn:
            await conn.execute("""
                UPDATE tasks SET is_active = FALSE WHERE id = $1 AND user_id = $2;
            """, task_id, user_id)

    async def add_daily_log(self, user_id: int, log_date: date, task_title: str, done_amount: str, note: str, mood: str, photo_file_id: str = None):
        async with self.pool.acquire() as conn:
            await conn.execute("""
                INSERT INTO daily_logs (user_id, log_date, task_title, done_amount, note, mood, photo_file_id)
                VALUES ($1, $2, $3, $4, $5, $6, $7);
            """, user_id, log_date, task_title, done_amount, note, mood, photo_file_id)
            return await self._update_streak_and_medals(conn, user_id, log_date)

    async def _update_streak_and_medals(self, conn, user_id: int, log_date: date):
        user = await conn.fetchrow("SELECT streak, max_streak, last_active_date, medals FROM users WHERE user_id = $1;", user_id)
        if not user:
            return 1, []
        
        streak = user['streak']
        max_streak = user['max_streak']
        last_date = user['last_active_date']
        medals = list(user['medals']) if user['medals'] else []
        new_medals = []

        if last_date is None:
            streak = 1
        elif last_date == log_date:
            pass
        elif last_date == log_date - timedelta(days=1):
            streak += 1
        else:
            streak = 1

        if streak > max_streak:
            max_streak = streak

        milestones = {
            7: "🥉 7 Kunlik Intizom Medali",
            30: "🥈 30 Kunlik Temir Iroda Medali",
            100: "🥇 100 Kunlik Afsona Medali",
            365: "🏆 365 Kunlik Mutlaq G'olib Kubogi"
        }

        for days, badge in milestones.items():
            if streak >= days and badge not in medals:
                medals.append(badge)
                new_medals.append(badge)

        await conn.execute("""
            UPDATE users 
            SET streak = $1, max_streak = $2, last_active_date = $3, medals = $4
            WHERE user_id = $5;
        """, streak, max_streak, log_date, medals, user_id)

        return streak, new_medals

    async def get_logs_by_range(self, user_id: int, start_date: date, end_date: date):
        async with self.pool.acquire() as conn:
            return await conn.fetch("""
                SELECT * FROM daily_logs 
                WHERE user_id = $1 AND log_date BETWEEN $2 AND $3
                ORDER BY log_date ASC, id ASC;
            """, user_id, start_date, end_date)
