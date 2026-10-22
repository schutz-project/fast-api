import json
from fastapi import Request
from sqlalchemy import select, func, update
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy import DateTime, ForeignKey, Integer, String, Text
from portal.models import Base, now

class Notice(Base):
    __tablename__='notices'
    id: Mapped[int]=mapped_column(Integer,primary_key=True)
    title: Mapped[str]=mapped_column(String(100))
    content: Mapped[str]=mapped_column(Text)
    created_at: Mapped[object]=mapped_column(DateTime(timezone=True),default=now)

class Post(Base):
    __tablename__='board_posts'
    id: Mapped[int]=mapped_column(Integer,primary_key=True)
    author_id: Mapped[int]=mapped_column(ForeignKey('user_accounts.user_id'))
    title: Mapped[str]=mapped_column(String(100))
    content: Mapped[str]=mapped_column(Text)
    created_at: Mapped[object]=mapped_column(DateTime(timezone=True),default=now)
    version: Mapped[int]=mapped_column(Integer,default=1)
    deleted_at: Mapped[object | None]=mapped_column(DateTime(timezone=True))

def register_content(app):
    from portal.main import ApiError, invalid
    factory=app.state.sessions
    def output(row,db,post=False):
        from portal.models import UserAccount
        data={'id':row.id,'title':row.title,'content':row.content,'created_at':row.created_at.isoformat()}
        if post:
            author=db.get(UserAccount,row.author_id)
            data.update(author_id=row.author_id,author=author.username if author else '알 수 없음',version=row.version)
        return data
    def payload(request):
        if request.headers.get('content-type','').split(';')[0]!='application/json': invalid('body')
        try: body=json.loads(request.state.raw_body)
        except (ValueError,UnicodeError): invalid('body')
        if not isinstance(body,dict): invalid('body')
        return body
    def fields(body):
        result={}
        for key,limit in [('title',100),('content',1000)]:
            value=body.get(key)
            if not isinstance(value,str) or not 1<=len(value.strip())<=limit: invalid(key)
            result[key]=value.strip()
        return result
    @app.get('/api/notices')
    def notices(request:Request):
        with factory.begin() as db:
            app.state.authenticate(request,db)
            rows=db.scalars(select(Notice).order_by(Notice.created_at.desc(),Notice.id.desc())).all()
            return {'items':[output(r,db) for r in rows],'count':len(rows)}
    @app.get('/api/posts')
    def posts(request:Request,page:int=1):
        if not 1<=page<=100000: invalid('page')
        with factory.begin() as db:
            app.state.authenticate(request,db)
            query=select(Post).where(Post.deleted_at.is_(None))
            total=db.scalar(select(func.count()).select_from(query.subquery()))
            rows=db.scalars(query.order_by(Post.created_at.desc(),Post.id.desc()).offset((page-1)*20).limit(20)).all()
            return {'items':[output(r,db,True) for r in rows],'page':page,'page_size':20,'total':total}
    @app.post('/api/posts',status_code=201)
    def create(request:Request):
        with factory.begin() as db:
            session,user,_=app.state.authenticate(request,db)
            app.state.csrf(request,session.csrf_token_hash)
            body=payload(request)
            if set(body)!={'title','content'}: invalid('body')
            row=Post(author_id=user.user_id,**fields(body)); db.add(row); db.flush()
            return output(row,db,True)
    @app.put('/api/posts/{post_id}')
    def edit(post_id:int,request:Request):
        return mutate(post_id,request,False)
    @app.delete('/api/posts/{post_id}')
    def delete(post_id:int,request:Request):
        return mutate(post_id,request,True)
    def mutate(post_id,request,deleting):
        with factory.begin() as db:
            session,user,_=app.state.authenticate(request,db)
            app.state.csrf(request,session.csrf_token_hash)
            row=db.scalar(select(Post).where(Post.id==post_id,Post.deleted_at.is_(None)).with_for_update())
            if row is None: raise ApiError(404,'NOT_FOUND','게시글이 없습니다.')
            if row.author_id!=user.user_id and not (deleting and user.role=='admin'):
                raise ApiError(403,'FORBIDDEN','본인의 게시글만 수정할 수 있습니다.')
            body=payload(request)
            if set(body)!=({'version'} if deleting else {'version','title','content'}): invalid('body')
            if type(body.get('version')) is not int or body['version']!=row.version:
                raise ApiError(409,'STATE_CONFLICT','게시글이 변경되었습니다. 새로고침해 주세요.')
            changes={'version':row.version+1}
            if deleting: changes['deleted_at']=now()
            else: changes.update(fields(body))
            count=db.execute(update(Post).where(Post.id==post_id,Post.version==row.version).values(**changes)).rowcount
            if count!=1: raise ApiError(409,'STATE_CONFLICT','게시글이 변경되었습니다. 새로고침해 주세요.')
            db.refresh(row)
            return {'status':'deleted'} if deleting else output(row,db,True)

def seed_notices(factory):
    rows=[('Schutz 포털 이용 안내','직원 이름으로 부서와 연락처를 확인할 수 있습니다. 모든 직원 정보는 실습용 가상 데이터입니다.'),('계정 보안 안내','공용 기기에서는 이용 후 로그아웃해 주세요. 비밀번호를 게시판에 올리지 마세요.'),('모니터링 실습 안내','장애 시험은 담당자와 시간을 합의한 뒤 진행합니다. 실제 업무 자료는 사용하지 않습니다.')]
    with factory.begin() as db:
        for i,(title,content) in enumerate(rows,1):
            if db.get(Notice,i) is None: db.add(Notice(id=i,title=title,content=content))
