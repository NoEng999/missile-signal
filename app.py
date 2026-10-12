import tkinter as tk
from tkinter import ttk, messagebox, scrolledtext
import threading, subprocess, json, re, queue, time, webbrowser
from PIL import ImageTk
import qrcode
from monitor import *

def main(smoke=False):
    root=tk.Tk(); root.title('北朝鮮ミサイル → Signal / PC試運転'); root.geometry('1030x780')
    config=load_config(); engine=Engine(config); jobs=queue.Queue(); daemon=[None]; linkproc=[None]; started=[False]
    frame=ttk.Frame(root,padding=16); frame.pack(fill='both',expand=True)
    ttk.Label(frame,text='ミサイル発射情報モニター',font=('Yu Gothic UI',20,'bold')).pack(anchor='w')
    ttk.Label(frame,text='第一報を優先送信 / 詳細は続報 / 情報源ごとの重複を許容 / PCの起動中のみ監視').pack(anchor='w',pady=(2,12))
    settings=ttk.LabelFrame(frame,text='設定',padding=10); settings.pack(fill='x')
    account=tk.StringVar(value=config['signal_account']); recipient=tk.StringVar(value=config['signal_recipient']); token=tk.StringVar(value=config['x_bearer_token']); interval=tk.StringVar(value=str(config['interval_seconds']))
    for row,(label,var,hide) in enumerate([('Signalアカウント（QR連携後に自動入力）',account,False),('送信先：空欄は自分のメモ / 他の端末は +国番号…',recipient,False),('X API Bearer Token（未設定ならXは停止）',token,True),('Web取得間隔（秒、最小5）',interval,False)]):
        ttk.Label(settings,text=label).grid(row=row,column=0,sticky='w',pady=3)
        ttk.Entry(settings,textvariable=var,width=64,show='*' if hide else '').grid(row=row,column=1,sticky='ew',padx=10)
    settings.columnconfigure(1,weight=1)
    group_id=tk.StringVar(value=config.get('signal_group_id',''))
    group_name=tk.StringVar(value=config.get('signal_group_name',''))
    groups={}
    destination=tk.StringVar(value='グループ' if group_id.get() else ('個別' if recipient.get() else '自分のメモ'))
    ttk.Label(settings,text='通知先の種類').grid(row=4,column=0,sticky='w',pady=3)
    ttk.Combobox(settings,textvariable=destination,values=['自分のメモ','個別','グループ'],state='readonly').grid(row=4,column=1,sticky='ew',padx=10)
    ttk.Label(settings,text='通知先グループ（参加済みグループから選択）').grid(row=5,column=0,sticky='w',pady=3)
    groupbox=ttk.Combobox(settings,textvariable=group_name,state='readonly')
    groupbox.grid(row=5,column=1,sticky='ew',padx=10)
    if group_id.get(): groups[group_name.get() or group_id.get()]=group_id.get(); group_name.set(next(iter(groups))); groupbox['values']=list(groups)
    def choose_group(event=None):
        group_id.set(groups.get(group_name.get(),'')); destination.set('グループ')
    groupbox.bind('<<ComboboxSelected>>',choose_group)
    bar=ttk.Frame(frame); bar.pack(fill='x',pady=10)
    summary=tk.StringVar(value='停止中 / Signal・Xの連携設定を行ってください')
    ttk.Label(frame,textvariable=summary).pack(anchor='w')
    tree=ttk.Treeview(frame,columns=('state','time'),show='tree headings',height=5)
    tree.heading('#0',text='情報源'); tree.heading('state',text='状態'); tree.heading('time',text='最終確認（日本時間）'); tree.column('#0',width=130); tree.column('state',width=560); tree.column('time',width=220); tree.pack(fill='x',pady=8)
    for name in ('防衛省','NHK RSS','NHK Web','X','Signal'): tree.insert('','end',iid=name,text=name,values=('未開始',''))
    ttk.Label(frame,text='通知履歴・動作ログ').pack(anchor='w')
    log=scrolledtext.ScrolledText(frame,height=18,wrap='word',font=('Yu Gothic UI',10)); log.pack(fill='both',expand=True)
    ttk.Label(frame,text='初回は既存記事を登録し、過去記事を通知しません。未連携中の通知は保存されます。自分のメモの通知音はスマートフォンで確認してください。',wraplength=980).pack(anchor='w',pady=8)
    def ui_log(t): log.insert('end',now()+' '+t+'\n\n'); log.see('end')
    def background(func):
        def work():
            try: func()
            except Exception as ex: jobs.put(('error',str(ex)))
        threading.Thread(target=work,daemon=True).start()
    def save():
        try: seconds=float(interval.get()); assert seconds>=5
        except Exception: messagebox.showerror('設定','取得間隔は5秒以上の数値にしてください'); return False
        if destination.get()=='グループ' and not group_id.get():
            messagebox.showerror('送信先','「グループ一覧を取得」で送信するグループを選んでください。'); return False
        if destination.get()=='個別' and not recipient.get().strip():
            messagebox.showerror('送信先','個別の送信先を入力してください。'); return False
        config.update(signal_account=account.get().strip(),signal_recipient=recipient.get().strip() if destination.get()=='個別' else '',signal_group_id=group_id.get() if destination.get()=='グループ' else '',signal_group_name=group_name.get() if destination.get()=='グループ' else '',x_bearer_token=token.get().strip(),interval_seconds=seconds)
        save_config(config); engine.config=dict(config); return True
    def ensure_daemon():
        if daemon[0] is None or daemon[0].poll() is not None: daemon[0]=start_daemon()
    def start():
        if started[0]: return
        if not save(): return
        if config['signal_account']:
            try: ensure_daemon()
            except Exception as ex: messagebox.showerror('Signal',str(ex)); return
        engine.start(); started[0]=True; summary.set('監視中（ウィンドウを閉じると停止）')
    def get_groups():
        if not account.get().strip(): messagebox.showinfo('Signal','先にSignalをQR連携してください。'); return
        ensure_daemon()
        def work():
            result=None
            for attempt in range(12):
                try:
                    r=requests.post(config['signal_rpc'],json={'jsonrpc':'2.0','method':'listGroups','params':{'account':account.get().strip()},'id':str(uuid.uuid4())},timeout=(3,15)); r.raise_for_status(); result=r.json(); break
                except requests.ConnectionError: time.sleep(.5)
            if result is None: raise RuntimeError('Signalの起動が完了していません。少し待って一覧を取得してください。')
            if result.get('error'): raise RuntimeError('Signalグループ一覧を取得できませんでした。')
            jobs.put(('groups',[g for g in result.get('result',[]) if g.get('isMember') and not g.get('isBlocked')]))
        background(work)
    ttk.Button(settings,text='グループ一覧を取得',command=get_groups).grid(row=6,column=1,sticky='w',padx=10,pady=5)
    def link():
        if linkproc[0] and linkproc[0].poll() is None: return
        if daemon[0] and daemon[0].poll() is None:
            messagebox.showinfo('連携','Signal連携済みです。再連携は監視画面を閉じてから行ってください。'); return
        ui_log('Signal連携を準備中。表示されるQRをスマートフォンのSignal → 設定 → リンク済みデバイスで読み取ってください。')
        def work():
            cmd,env=signal_cmd('link','-n','MissileSignal-PC')
            p=subprocess.Popen(cmd,env=env,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True,encoding='utf-8',errors='replace',creationflags=subprocess.CREATE_NO_WINDOW); linkproc[0]=p
            for line in p.stdout:
                match=re.search(r'(?:sgnl://linkdevice\?\S+|tsdevice:/\?\S+)',line)
                if match: jobs.put(('qr',match.group(0)))
            if p.wait()!=0: raise RuntimeError('Signal連携が完了しませんでした。QRの期限が切れた場合はもう一度連携してください。')
            cmd,env=signal_cmd('-o','json','listAccounts')
            r=subprocess.run(cmd,env=env,capture_output=True,text=True,encoding='utf-8',errors='replace',timeout=30,creationflags=subprocess.CREATE_NO_WINDOW)
            if r.returncode: raise RuntimeError('連携アカウントの読込に失敗しました')
            entries=json.loads(r.stdout)
            if isinstance(entries,dict): entries=entries.get('accounts',[entries])
            a=entries[0]; identifier=a.get('number') or a.get('account') or a.get('aci') or a.get('uuid')
            if not identifier: raise RuntimeError('アカウントIDを取得できません')
            jobs.put(('linked',identifier))
        background(work)
    def test():
        if not save(): return
        if not config['signal_account']: messagebox.showinfo('Signal','先に「SignalをQR連携」を押して連携してください。'); return
        if not started[0]: start()
        e={'source':'模擬テスト','title':'北朝鮮がミサイルを発射したという模擬情報','text':'北朝鮮が弾道ミサイルを2発発射した。EEZ外に落下する見込み。領海への落下情報は未発表。','url':'https://www.mod.go.jp/j/surround/northKorea/index.html','published':now(),'detected':now(),'test':True}
        engine.enqueue(message(e)); engine.enqueue(message(e,True),10)
    def clear():
        with engine.lock: engine.db.execute('DELETE FROM outbox WHERE sent IS NULL'); engine.db.commit()
        ui_log('未送信キューを削除しました。')
    ttk.Button(bar,text='SignalをQR連携',command=link).pack(side='left',padx=3)
    ttk.Button(bar,text='設定を保存',command=save).pack(side='left',padx=3)
    ttk.Button(bar,text='監視開始',command=start).pack(side='left',padx=3)
    ttk.Button(bar,text='模擬発射をSignalへ送信',command=test).pack(side='left',padx=3)
    ttk.Button(bar,text='未送信キューを消去',command=clear).pack(side='left',padx=3)
    qrwindow=[None]
    def tick():
        while not engine.events.empty():
            dt,msg=engine.events.get(); log.insert('end',dt+' '+msg+'\n\n'); log.see('end')
        while not jobs.empty():
            kind,value=jobs.get()
            if kind=='error': messagebox.showerror('操作エラー',value); ui_log(value)
            elif kind=='groups':
                groups.clear()
                for g in value: groups[(g.get('name') or '名前なし')+' ['+g['id'][:10]+']']=g['id']
                groupbox['values']=list(groups)
                matches=[name for name,gid in groups.items() if gid==group_id.get()]
                if matches: group_name.set(matches[0])
                else: group_name.set(''); group_id.set('')
                ui_log(f'参加済みグループ {len(groups)}件を取得。通知先を選び「設定を保存」を押してください。')
            elif kind=='qr':
                win=tk.Toplevel(root); qrwindow[0]=win; win.title('Signalデバイス連携'); image=ImageTk.PhotoImage(qrcode.make(value).resize((400,400))); label=ttk.Label(win,image=image); label.image=image; label.pack(padx=20,pady=20)
                ttk.Label(win,text='スマートフォンのSignal → 設定 → リンク済みデバイス\n→ デバイスを追加 → このQRを読み取る',justify='center').pack(pady=15)
            elif kind=='linked':
                account.set(value); save(); ensure_daemon(); ui_log('Signal連携完了。模擬発射の送信で受信を確認してください。')
                if qrwindow[0] and qrwindow[0].winfo_exists(): qrwindow[0].destroy()
        with engine.lock: states=dict(engine.status)
        for name,s in states.items(): tree.item(name,values=(s['state'],s['checked']))
        total,pending=engine.counts()
        if started[0]: summary.set(f'監視中 / 通知レコード {total}件 / 未送信 {pending or 0}件 / Web間隔 {config["interval_seconds"]}秒')
        root.after(250,tick)
    def close():
        engine.close()
        for p in (daemon[0],linkproc[0]):
            if p and p.poll() is None:
                subprocess.run(['taskkill','/PID',str(p.pid),'/T','/F'],capture_output=True,creationflags=subprocess.CREATE_NO_WINDOW)
        root.destroy()
    root.protocol('WM_DELETE_WINDOW',close); tick()
    if smoke: root.after(800,close)
    else: root.after(500,start)
    root.mainloop()

if __name__=='__main__':
    import sys
    main('--smoke-ui' in sys.argv)
