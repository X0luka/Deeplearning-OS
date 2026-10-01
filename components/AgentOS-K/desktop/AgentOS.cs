using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Drawing;
using System.IO;
using System.Linq;
using System.Security.Cryptography;
using System.Threading;
using System.Threading.Tasks;
using System.Web.Script.Serialization;
using System.Windows.Forms;
using Microsoft.Web.WebView2.Core;
using Microsoft.Web.WebView2.WinForms;

[assembly: System.Reflection.AssemblyTitle("Deeplearning-OS")]
[assembly: System.Reflection.AssemblyProduct("Deeplearning-OS")]

class AgentOS : Form {
    readonly WebView2 view = new WebView2();
    readonly Panel content = new Panel();
    readonly Panel workspace = new Panel();
    readonly Button returnConversation = new Button();
    string citationOrigin;
    readonly FlowLayoutPanel navigation = new FlowLayoutPanel();
    readonly Dictionary<string, WebView2> panes = new Dictionary<string, WebView2>();
    readonly Dictionary<string, Task> initialization = new Dictionary<string, Task>();
    readonly Dictionary<string, Button> navButtons = new Dictionary<string, Button>();
    readonly Dictionary<string, string> productRoutes = new Dictionary<string, string>();
    readonly ToolTip navTips = new ToolTip();
    CoreWebView2Environment environment;
    string activePane = "home";
    int navigationGeneration;
    readonly Label status = new Label();
    readonly Button retry = new Button();
    readonly JavaScriptSerializer json = new JavaScriptSerializer();
    readonly HashSet<string> productOrigins = new HashSet<string>();
    readonly string logDir;
    readonly DesktopConfiguration settings;
    Process session;
    string token, hostOrigin;
    bool closing, finished, waiting;
    System.Windows.Forms.Timer waitTimer;
    static EventWaitHandle focus;

    [STAThread] static void Main() {
        DesktopConfiguration settings;
        try { settings = DesktopConfiguration.Load(Path.Combine(AppDomain.CurrentDomain.BaseDirectory, "desktop.json")); }
        catch (Exception ex) { MessageBox.Show(ex.Message, "开发版启动配置未通过"); return; }
        bool first;
        using (var mutex = new Mutex(true, settings.Identity + ".Desktop", out first)) {
            focus = new EventWaitHandle(false, EventResetMode.AutoReset, settings.Identity + ".Focus");
            if (!first) { focus.Set(); return; }
            Application.EnableVisualStyles();
            Application.SetCompatibleTextRenderingDefault(false);
            var form = new AgentOS(settings, true);
            var thread = new Thread(() => { while (true) { focus.WaitOne(); if (form.IsHandleCreated) form.BeginInvoke((Action)(() => { form.WindowState = FormWindowState.Normal; form.Show(); form.Activate(); })); } });
            thread.IsBackground = true; thread.Start();
            Application.Run(form);
        }
    }

    AgentOS() : this(true) {}
    internal AgentOS(bool startServices) : this(startServices ? DesktopConfiguration.Load(Path.Combine(AppDomain.CurrentDomain.BaseDirectory, "desktop.json")) : null, startServices) {}
    internal AgentOS(DesktopConfiguration configuration, bool startServices) {
        settings = configuration;
        logDir = Path.Combine(settings != null ? settings.DataDirectory : Path.Combine(AppDomain.CurrentDomain.BaseDirectory, "test-state"), "logs");
        Text = settings != null ? settings.Title : "Deeplearning-OS · 隔离测试"; Icon = Icon.ExtractAssociatedIcon(Application.ExecutablePath); Width = 1200; Height = 850; MinimumSize = new Size(750, 500);
        StartPosition = FormStartPosition.CenterScreen;
        view.Dock = DockStyle.Fill;
        status.Dock = DockStyle.Top; status.Height = 36; status.Padding = new Padding(12,8,0,0);
        status.TextChanged += (a,b) => { status.Visible = !String.IsNullOrEmpty(status.Text); };
        status.Text = "正在启动 Deeplearning-OS…";
        retry.Text = "重试启动"; retry.Dock = DockStyle.Bottom; retry.Height = 36; retry.Visible = false;
        retry.Click += async (s,e) => {
            if(waiting) {waiting=false;closing=false;if(waitTimer!=null)waitTimer.Stop();retry.Visible=false;status.Text="已取消退出，可继续使用。";return;}
            try { await StartSession(); } catch(Exception ex) { Fail(ex.Message); }
        };
        content.Dock = DockStyle.Fill; content.Controls.Add(view); panes.Add("home", view);
        navigation.Dock = DockStyle.Left; navigation.Width = 76; navigation.FlowDirection = FlowDirection.TopDown;
        navigation.WrapContents = false; navigation.AutoScroll = true; navigation.Padding = new Padding(5, 10, 5, 5);
        navigation.BackColor = Color.FromArgb(32,32,32);
        AddNavigation("home", "⌂", "主页", "返回主菜单");
        AddNavigation("assistant", "☷", "会话", "助手会话");
        AddNavigation("data", "▱", "资料", "数据与知识中心");
        AddNavigation("models", "⚙", "模型", "API 与模型");
        AddNavigation("github-wiki", "</>", "Wiki", "源码阅读");
        AddNavigation("paper", "▤", "Reader", "论文阅读");
        var more = new Button {Text="⋯", Width=62, Height=40, FlatStyle=FlatStyle.Flat, ForeColor=Color.White, BackColor=navigation.BackColor, AccessibleName="更多页面操作"};
        more.FlatAppearance.BorderSize=0;navTips.SetToolTip(more,"更多页面操作");navigation.Controls.Add(more);
        more.Click += (a,b) => {
            var menu = new ContextMenuStrip();
            var back=menu.Items.Add("返回上一页");back.Enabled=panes[activePane].CoreWebView2!=null && panes[activePane].CoreWebView2.CanGoBack;
            back.Click+=(x,y)=>panes[activePane].CoreWebView2.GoBack();
            var external=menu.Items.Add("在浏览器中打开当前页面");external.Enabled=panes[activePane].Source!=null;
            external.Click+=(x,y)=>OpenExternal(panes[activePane].Source.AbsoluteUri);
            menu.Closed+=(x,y)=>menu.Dispose();menu.Show(more,new Point(more.Width,0));
        };
        workspace.Dock=DockStyle.Fill;workspace.Controls.Add(content);
        returnConversation.Text="← 返回原会话";returnConversation.AccessibleName="返回原会话";
        returnConversation.Dock=DockStyle.Top;returnConversation.Height=40;returnConversation.TextAlign=ContentAlignment.MiddleLeft;returnConversation.Visible=false;
        returnConversation.Click+=async (a,b)=>{if(citationOrigin!=null)try{await ActivatePane(citationOrigin,null);}catch(Exception ex){status.Text="返回失败："+ex.Message;}};
        workspace.Controls.Add(returnConversation);
        Controls.Add(workspace); Controls.Add(navigation); Controls.Add(status); Controls.Add(retry);
        if(startServices) Shown += async (s,e) => {
            try {
                CoreWebView2Environment.GetAvailableBrowserVersionString();
                environment = await CoreWebView2Environment.CreateAsync(null, Path.Combine(settings.DataDirectory, "WebView2"));
                await InitializePane("home", view);
                await StartSession();
            } catch (WebView2RuntimeNotFoundException) { status.Text = "缺少 Microsoft Edge WebView2 Runtime。请安装后重新打开 Deeplearning-OS。"; }
              catch (Exception ex) { Fail(ex.Message); }
        };
        FormClosing += (s,e) => {
            if (finished || session == null || session.HasExited) return;
            e.Cancel = true;
            if (closing) return;
            closing = true; status.Text = "正在检查后台任务…";
            Send("prepare");
        };
        FormClosed += (s,e) => { if (session != null) { try { session.StandardInput.Close(); } catch {} session.Dispose(); } };
    }

    void AddNavigation(string key, string icon, string label, string description) {
        var button = new Button {Text=icon+"\n"+label, Width=62, Height=64, FlatStyle=FlatStyle.Flat,
            ForeColor=Color.FromArgb(235,235,235), BackColor=navigation.BackColor, Font=new Font("Segoe UI",11),
            AccessibleName=description, Enabled=false, Margin=new Padding(0,2,0,2)};
        button.FlatAppearance.BorderSize=0;navTips.SetToolTip(button,description);navButtons.Add(key,button);navigation.Controls.Add(button);
        button.Click += async (a,b) => {try {await ActivatePane(key,null);} catch(Exception ex) {status.Text="页面打开失败："+ex.Message;}};
    }
    string PaneFor(string url, string fallback) {
        Uri uri;
        if(!Uri.TryCreate(url,UriKind.Absolute,out uri))return null;
        string origin=uri.GetLeftPart(UriPartial.Authority);
        if(origin==hostOrigin) {
            foreach(var id in productRoutes.Keys)if(uri.AbsolutePath=="/products/"+id+"/open")return id;
            if(uri.AbsolutePath=="/assistant")return "assistant";
            if(uri.AbsolutePath=="/data"||uri.AbsolutePath.StartsWith("/data/"))return "data";
            if(uri.AbsolutePath.StartsWith("/settings/models"))return "models";
            if(uri.AbsolutePath=="/"||uri.AbsolutePath.StartsWith("/history/"))return "home";
            return fallback;
        }
        foreach(var pair in productRoutes)if(new Uri(pair.Value).GetLeftPart(UriPartial.Authority)==origin)return pair.Key;
        return null;
    }
    async Task InitializePane(string key, WebView2 pane) {
        await pane.EnsureCoreWebView2Async(environment);
        pane.CoreWebView2.Settings.AreDevToolsEnabled=false;
        // Only the desktop top-level document hides the web rail. Browser pages and embedded assistants retain their existing behavior.
        await pane.CoreWebView2.AddScriptToExecuteOnDocumentCreatedAsync(@"if(window===window.top){
          window.__aosDesktopShell=true;
          document.addEventListener('DOMContentLoaded',()=>{
            const style=document.createElement('style');style.textContent='.aos-primary-nav{display:none!important}.aos-workspace-shell>main{margin-left:0!important}.aos-assistant-shell{padding-left:0!important}';document.head.append(style);
          });
        }");
        pane.CoreWebView2.NewWindowRequested += async (a,b) => {
            b.Handled=true;string target=PaneFor(b.Uri,key);
            try {if(target!=null)await ActivatePane(key=="citation"?"citation":target,b.Uri);else if(b.IsUserInitiated)OpenExternal(b.Uri);}catch(Exception ex){status.Text="页面打开失败："+ex.Message;}
        };
        pane.CoreWebView2.NavigationStarting += async (a,b) => {
            string target=PaneFor(b.Uri,key);
            if(target==key || (key=="citation" && target!=null))return;
            b.Cancel=true;
            try {if(target!=null)await ActivatePane(key=="citation"?"citation":target,b.Uri);else if(b.IsUserInitiated)OpenExternal(b.Uri);}catch(Exception ex){status.Text="页面打开失败："+ex.Message;}
        };
        pane.CoreWebView2.WebMessageReceived += async (a,b) => {
            Uri source;
            if(!Uri.TryCreate(b.Source,UriKind.Absolute,out source) || PaneFor(b.Source,key)==null)return;
            string message;
            try{message=b.TryGetWebMessageAsString();}catch{return;}
            if(message=="exit" && source.GetLeftPart(UriPartial.Authority)==hostOrigin){Close();return;}
            if(activePane!=key || message==null || message.Length>8192)return;
            try{
                var request=json.Deserialize<Dictionary<string,object>>(message);
                if(request!=null && request.ContainsKey("type") && Convert.ToString(request["type"])=="open-citation" && request.ContainsKey("url"))
                    await OpenCitation(key,Convert.ToString(request["url"]));
            }catch(Exception ex){status.Text="引用未打开："+ex.Message;}
        };
        pane.CoreWebView2.NavigationCompleted += (a,b) => {
            if(key==activePane)status.Text=b.IsSuccess?String.Empty:"页面暂不可用，可切换其他功能或在更多菜单中返回。";
        };
    }
    async Task OpenCitation(string origin, string url) {
        Uri target;
        if(!Uri.TryCreate(url,UriKind.Absolute,out target) || (target.Scheme!="http" && target.Scheme!="https") || target.UserInfo.Length!=0)return;
        if(PaneFor(url,origin)==null){OpenExternal(url);return;}
        if(origin!="citation")citationOrigin=origin;
        if(citationOrigin==null)return;
        await ActivatePane("citation",url);
    }
    async Task ActivatePane(string key, string destination) {
        if(hostOrigin==null)return;
        int generation=++navigationGeneration;
        WebView2 pane;
        if(!panes.TryGetValue(key,out pane)){
            pane=new WebView2 {Dock=DockStyle.Fill,Visible=false};panes.Add(key,pane);content.Controls.Add(pane);
            initialization[key]=InitializePane(key,pane);
        }
        Task ready;if(initialization.TryGetValue(key,out ready))await ready;
        if(generation!=navigationGeneration)return;
        string target=destination;
        if(pane.Source==null || pane.Source.Scheme=="about"){
            if(target==null)target=hostOrigin+(key=="home"?"/":key=="assistant"?"/assistant":key=="data"?"/data":key=="models"?"/settings/models":"/products/"+key+"/open");
        } else if(target!=null){
            var uri=new Uri(target);
            // Selecting a product entry again must not reload its retained document.
            if(uri.GetLeftPart(UriPartial.Authority)==hostOrigin && uri.AbsolutePath=="/products/"+key+"/open")target=null;
            else if(productRoutes.ContainsKey(key) && uri.AbsolutePath=="/" && string.IsNullOrEmpty(uri.Query))target=null;
            else if(pane.Source.AbsoluteUri==target)target=null;
        }
        activePane=key;returnConversation.Visible=key=="citation" && citationOrigin!=null;pane.Visible=true;pane.BringToFront();
        foreach(var pair in panes)if(pair.Key!=key)pair.Value.Visible=false;
        foreach(var pair in navButtons)pair.Value.BackColor=pair.Key==key?Color.FromArgb(65,65,65):navigation.BackColor;
        if(target!=null)pane.CoreWebView2.Navigate(target);
        pane.Focus();status.Text=String.Empty;
    }
    void OpenExternal(string url) {
        Uri uri;
        if(!Uri.TryCreate(url,UriKind.Absolute,out uri)||(uri.Scheme!="http"&&uri.Scheme!="https"))return;
        Process.Start(new ProcessStartInfo(uri.AbsoluteUri) {UseShellExecute=true});
    }
    void Fail(string message) {
        status.Text = "启动未完成：" + message; retry.Text="重试启动";retry.Visible = true;
        retry.Enabled = session == null || session.HasExited;
    }
    async Task StartSession() {
        if (session != null && !session.HasExited) return;
        retry.Visible = false; finished = false; closing = false; waiting = false;
        Directory.CreateDirectory(logDir);
        byte[] bytes = new byte[32]; using(var rng=RandomNumberGenerator.Create()) rng.GetBytes(bytes); token=Convert.ToBase64String(bytes);
        var info = new ProcessStartInfo(Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.System), "wsl.exe"), settings.WslArguments) {
            UseShellExecute=false, CreateNoWindow=true, RedirectStandardInput=true, RedirectStandardOutput=true, RedirectStandardError=true
        };
        var current = new Process { StartInfo=info, EnableRaisingEvents=true };
        current.Start();
        session = current;
        session.StandardInput.AutoFlush = true;
        status.Text="正在启动工作台、Wiki 和 Reader…";
        // Drain both streams without inheriting any pipe handles into product browser processes.
        var errors = Task.Run(async () => {
            using(var log=new StreamWriter(Path.Combine(logDir,"session-stderr.log"),true)) {
                string line; while((line=await current.StandardError.ReadLineAsync())!=null) { await log.WriteLineAsync(line); await log.FlushAsync(); }
            }
        });
        _Read(current, errors);
        try { await session.StandardInput.WriteLineAsync(json.Serialize(new {token=token})); }
        catch (IOException) { File.AppendAllText(Path.Combine(logDir,"launcher.log"),"Handshake pipe closed"+Environment.NewLine); }
    }
    async void _Read(Process current, Task errors) {
        try {
            string line;
            while((line=await current.StandardOutput.ReadLineAsync())!=null) {
                File.AppendAllText(Path.Combine(logDir,"launcher.log"),"stdout: "+line+Environment.NewLine);
                Dictionary<string,object> message;
                try { message=json.Deserialize<Dictionary<string,object>>(line); } catch { File.AppendAllText(Path.Combine(logDir,"launcher.log"),line+Environment.NewLine); status.Text=line; continue; }
                if (message == null || !message.ContainsKey("event")) continue;
                var kind=Convert.ToString(message["event"]);
                if(kind=="ready") {
                    hostOrigin=new Uri(Convert.ToString(message["url"])).GetLeftPart(UriPartial.Authority);
                    productOrigins.Clear();
                    foreach(var url in (System.Collections.ArrayList)message["products"]) productOrigins.Add(new Uri(Convert.ToString(url)).GetLeftPart(UriPartial.Authority));
                    productRoutes.Clear();
                    if(message.ContainsKey("product_routes"))foreach(var pair in (Dictionary<string,object>)message["product_routes"])productRoutes[pair.Key]=Convert.ToString(pair.Value);
                    foreach(var pair in navButtons)pair.Value.Enabled=pair.Key=="home"||pair.Key=="assistant"||pair.Key=="data"||pair.Key=="models"||productRoutes.ContainsKey(pair.Key);
                    if (!closing) { await ActivatePane("home", Convert.ToString(message["url"])); status.Text="Deeplearning-OS · 关闭此窗口将退出本次会话"; }
                } else if(kind=="prepared") {
                    if(!closing) continue; // Ignore an in-flight reply after cancelling the wait.
                    var busy=(System.Collections.ArrayList)message["busy"];
                    if(busy.Count>0 && !waiting) {
                        var choice=MessageBox.Show(this, "应用仍有任务或无法确认任务状态："+string.Join("、",busy.Cast<object>())+"\n是：等待完成；否：确认停止；取消：继续使用。", "退出 Deeplearning-OS",MessageBoxButtons.YesNoCancel,MessageBoxIcon.Question);
                        if(choice==DialogResult.Cancel) {closing=false;status.Text="已取消退出";continue;}
                        waiting=choice==DialogResult.Yes;
                    }
                    if(waiting && busy.Count>0) {
                        status.Text="等待任务完成，再退出 Deeplearning-OS…";
                        retry.Text="取消等待，继续使用";retry.Enabled=true;retry.Visible=true;
                        if(waitTimer==null) { waitTimer=new System.Windows.Forms.Timer {Interval=2000}; waitTimer.Tick+=(s,e)=>{waitTimer.Stop();Send("prepare");}; }
                        waitTimer.Start();
                    } else { if(waitTimer!=null)waitTimer.Stop();retry.Visible=false; Send("exit");status.Text="正在停止本次启动的服务，请稍候…"; }
                } else if(kind=="stopping") status.Text="正在停止："+message["service"]+"（最多等待 30 秒）";
                else if(kind=="error") Fail(Convert.ToString(message["message"]));
                else if(kind=="closed") { finished=true; }
            }
            await errors;
            await Task.Run(()=>current.WaitForExit());
            if(closing || finished) {finished=true;Close();}
            else { File.AppendAllText(Path.Combine(logDir,"launcher.log"),"WSL exit="+current.ExitCode+Environment.NewLine); Fail("后台会话已结束（"+current.ExitCode+"），请查看日志或重试。"); }
        } catch(Exception ex) {
            File.AppendAllText(Path.Combine(logDir,"launcher.log"),ex.ToString()+Environment.NewLine);
            try { current.StandardInput.Close(); } catch {}
            var failure = ex.Message;
            current.Exited += (s,e)=> {
                if(!IsDisposed)BeginInvoke((Action)(()=>Fail(failure)));
            };
            if(!IsDisposed)Fail(failure);
        }
    }
    void Send(string action) {
        try { session.StandardInput.WriteLine(json.Serialize(new {token=token,action=action})); }
        catch { status.Text="后台连接已关闭，等待清理完成…"; }
    }
    static string Quote(string value) {
        // WSL's distribution parser treats superfluous quotes as literal on this host.
        // Installation uses fixed, space-free identifiers and Linux paths.
        if(string.IsNullOrEmpty(value) || value.Any(c=>char.IsWhiteSpace(c) || c=='"' || c=='\0')) throw new ArgumentException("启动配置不能包含空白或引号。");
        return value;
    }
}
