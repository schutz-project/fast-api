from sqlalchemy import select
from portal.models import Employee, UserAccount
from portal.security import password_hasher

ROWS = [
 ('김민준','개발팀','팀장','101'),('이서연','개발팀','매니저','102'),('박지훈','개발팀','매니저','103'),
 ('최유진','인사총무팀','팀장','201'),('정하늘','인사총무팀','매니저','202'),('한지우','운영팀','팀장','301'),
 ('오도윤','운영팀','매니저','302'),('강수빈','운영팀','매니저','303'),('윤태호','보안팀','매니저','401'),('송다은','기획팀','매니저','501')]

def seed(factory, admin_password, user_password):
    if not all(12 <= len(p) <= 128 for p in [admin_password, user_password]):
        raise ValueError('Seed passwords must be 12..128 characters')
    with factory.begin() as db:
        for i,(name,department,position,extension) in enumerate(ROWS,1):
            code=f'EMP{i:03}'
            if db.scalar(select(Employee).where(Employee.employee_code==code)) is None:
                db.add(Employee(employee_code=code,name=name,department=department,position=position,email=f'emp{i:03}@portal.test',extension=extension))
        db.flush()
        for username,code,role,password in [('admin01','EMP004','admin',admin_password),('test_user','EMP002','user',user_password)]:
            if db.scalar(select(UserAccount).where(UserAccount.username==username)) is None:
                employee = db.scalar(select(Employee).where(Employee.employee_code==code))
                db.add(UserAccount(employee_id=employee.employee_id,username=username,role=role,password_hash=password_hasher.hash(password)))
    # Re-running never overwrites existing passwords, profile edits or session state.
    from portal.content import seed_notices
    seed_notices(factory)

if __name__ == '__main__':
    import os
    from dotenv import load_dotenv
    load_dotenv()
    from portal.config import Settings
    from portal.db import build_engine, session_factory
    config=Settings()
    engine=build_engine(config.database_url)
    try:
        seed(session_factory(engine),os.environ['SEED_ADMIN_PASSWORD'],os.environ['SEED_USER_PASSWORD'])
        print('Seed completed: existing rows preserved.')
    finally:
        engine.dispose()
