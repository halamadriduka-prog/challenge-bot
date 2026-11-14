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
                    days_of_week TEXT DEFAULT 'all',
                    is_active BOOLEAN DEFAULT TRUE,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
            """)
            await conn.execute("""
                ALTER TABLE tasks ADD COLUMN IF NOT EXISTS days_of_week TEXT DEFAULT 'all';
            """)
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS daily_logs (
                    id SERIAL PRIMARY KEY,
                    user_id BIGINT REFERENCES users(user_id) ON DELETE CASCADE,
                    log_date DATE NOT NULL,
                    task_id INT,
                    task_title TEXT,
                    done_amount TEXT,
                    status TEXT DEFAULT 'done',
                    note TEXT,
                    mood TEXT,
                    photo_file_id TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
            """)
            await conn.execute("""
                ALTER TABLE daily_logs ADD COLUMN IF NOT EXISTS task_id INT;
            """)
            await conn.execute("""
                ALTER TABLE daily_logs ADD COLUMN IF NOT EXISTS status TEXT DEFAULT 'done';
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

    async def add_task(self, user_id: int, title: str, target_amount: str, days_of_week: str = "all"):
        async with self.pool.acquire() as conn:
            await conn.execute("""
                INSERT INTO tasks (user_id, title, target_amount, days_of_week)
                VALUES ($1, $2, $3, $4);
            """, user_id, title, target_amount, days_of_week)

    async def get_active_tasks(self, user_id: int):
        async with self.pool.acquire() as conn:
            return await conn.fetch("""
                SELECT id, title, target_amount, days_of_week FROM tasks 
                WHERE user_id = $1 AND is_active = TRUE ORDER BY id ASC;
            """, user_id)

    async def get_today_tasks(self, user_id: int, weekday_idx: int):
        all_tasks = await self.get_active_tasks(user_id)
        today_tasks = []
        w_str = str(weekday_idx)
        for t in all_tasks:
            days = t['days_of_week'] or 'all'
            if days == 'all' or w_str in days.split(','):
                today_tasks.append(t)
        return today_tasks

    async def delete_task(self, task_id: int, user_id: int):
        async with self.pool.acquire() as conn:
            await conn.execute("""
                UPDATE tasks SET is_active = FALSE WHERE id = $1 AND user_id = $2;
            """, task_id, user_id)

    async def get_task_statuses_for_date(self, user_id: int, log_date: date):
        async with self.pool.acquire() as conn:
            rows = await conn.fetch("""
                SELECT task_id, status FROM daily_logs 
                WHERE user_id = $1 AND log_date = $2 AND task_id IS NOT NULL;
            """, user_id, log_date)
            return {r['task_id']: (r['status'] or 'done') for r in rows}

    async def set_task_status(self, user_id: int, task_id: int, log_date: date, new_status: str):
        async with self.pool.acquire() as conn:
            existing = await conn.fetchrow("""
                SELECT id, status FROM daily_logs 
                WHERE user_id = $1 AND task_id = $2 AND log_date = $3;
            """, user_id, task_id, log_date)

            task = await conn.fetchrow("SELECT title, target_amount FROM tasks WHERE id = $1;", task_id)
            if not task:
                return "none", 0, []

            # Agar o'sha tugma qayta bosilsa, holatni tozalash (bekor qilish)
            if existing and (existing['status'] or 'done') == new_status:
                await conn.execute("DELETE FROM daily_logs WHERE id = $1;", existing['id'])
                return "cleared", 0, []

            if existing:
                await conn.execute("DELETE FROM daily_logs WHERE id = $1;", existing['id'])

            if new_status == "done":
                done_label = f"✅ Bajarildi ({task['target_amount']})"
            else:
                done_label = f"❌ Qilinmadi ({task['target_amount']})"

            await conn.execute("""
                INSERT INTO daily_logs (user_id, log_date, task_id, task_title, done_amount, status)
                VALUES ($1, $2, $3, $4, $5, $6);
            """, user_id, log_date, task_id, task['title'], done_label, new_status)

            if new_status == "done":
                streak, new_medals = await self._update_streak_and_medals(conn, user_id, log_date)
                return "done", streak, new_medals
            else:
                return "missed", 0, []

    async def add_daily_journal(self, user_id: int, log_date: date, note: str, mood: str, photo_file_id: str = None):
        async with self.pool.acquire() as conn:
            await conn.execute("""
                INSERT INTO daily_logs (user_id, log_date, task_title, done_amount, status, note, mood, photo_file_id)
                VALUES ($1, $2, $3, $4, 'journal', $5, $6, $7);
            """, user_id, log_date, "📝 Kunlik xulosa", "Kundalik yozuv", note, mood, photo_file_id)
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
            if streak == 0:
                streak = 1
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
