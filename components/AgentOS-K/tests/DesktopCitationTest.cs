using System;
using System.IO;
using System.Text;
using System.Reflection;
using System.Collections.Generic;
using System.Threading.Tasks;
using System.Windows.Forms;
using Microsoft.Web.WebView2.Core;
using Microsoft.Web.WebView2.WinForms;

class PaneTest {
    static string root = AppDomain.CurrentDomain.BaseDirectory;
    static BindingFlags flags = BindingFlags.Instance|BindingFlags.NonPublic;
    static object Get(object f,string name) {return f.GetType().GetField(name,flags).GetValue(f);}
    static void Set(object f,string name,object value) {f.GetType().GetField(name,flags).SetValue(f,value);}
    static Task Call(object f,string name,params object[] args) {return (Task)f.GetType().GetMethod(name,flags).Invoke(f,args);}
    static void Check(bool good,string message) {if(!good)throw new Exception(message);}
    static async Task Loaded(WebView2 p) {
        for(int i=0;i<100;i++) {if(p.CoreWebView2!=null && await p.CoreWebView2.ExecuteScriptAsync("!!document.getElementById('draft')")=="true")return;await Task.Delay(100);}
        throw new Exception("Page did not load");
    }
    static void SyntheticPage(WebView2 pane) {
        pane.CoreWebView2InitializationCompleted += (sender, args) => {
            if (!args.IsSuccess) return;
            pane.CoreWebView2.AddWebResourceRequestedFilter("*",CoreWebView2WebResourceContext.All);
            pane.CoreWebView2.WebResourceRequested += (source, request) => {
                var uri=new Uri(request.Request.Uri);
                bool allowed=uri.Scheme=="http" && (uri.Host=="workbench.test"||uri.Host=="wiki.test"||uri.Host=="reader.test");
                bool failed=allowed && uri.AbsolutePath=="/fail";
                string html=failed ? "<h1 id=fixture-failure>Service unavailable</h1>" : allowed ? "<html><body><nav class='aos-primary-nav'>web nav</nav><textarea id='draft'></textarea><a id='wiki' target='_blank' href='http://wiki.test/'>Wiki</a><a id='reader' target='_blank' href='http://reader.test/'>Reader</a><a id='assistant' href='http://workbench.test/assistant'>Chat</a><div style='height:3000px'>fixture</div></body></html>" : "Blocked by test fixture";
                request.Response=pane.CoreWebView2.Environment.CreateWebResourceResponse(new MemoryStream(Encoding.UTF8.GetBytes(html)),failed?503:allowed?200:403,failed?"Service Unavailable":allowed?"OK":"Forbidden","Content-Type: text/html; charset=utf-8");
            };
        };
    }
    [STAThread] static void Main() {
        Application.EnableVisualStyles();Application.SetCompatibleTextRenderingDefault(false);
        var form=new AgentOS(false);form.ShowInTaskbar=false;form.StartPosition=FormStartPosition.Manual;form.Left=-14000;
        SyntheticPage((WebView2)Get(form,"view"));
        ((Panel)Get(form,"content")).ControlAdded+=(sender,args)=>{var pane=args.Control as WebView2;if(pane!=null)SyntheticPage(pane);};
        var deadline=new System.Windows.Forms.Timer {Interval=60000};
        deadline.Tick+=(sender,args)=>{File.WriteAllText(Path.Combine(root,"citation-result.txt"),"FAIL: 60 second UI deadline");Environment.Exit(124);};
        deadline.Start();
        form.Load+=async (s,e)=>{
            try {
                File.WriteAllText(Path.Combine(root,"test-progress.txt"),"Creating WebView environment");var env=await CoreWebView2Environment.CreateAsync(null,Path.Combine(root,"test-profile"),new CoreWebView2EnvironmentOptions("--disable-background-networking --disable-component-update"));
                File.AppendAllText(Path.Combine(root,"test-progress.txt"),"; environment ready");Set(form,"environment",env);Set(form,"hostOrigin","http://workbench.test");
                var routes=(Dictionary<string,string>)Get(form,"productRoutes");routes["github-wiki"]="http://wiki.test/";routes["paper"]="http://reader.test/";
                var panes=(Dictionary<string,WebView2>)Get(form,"panes");var buttons=(Dictionary<string,Button>)Get(form,"navButtons");foreach(var b in buttons.Values)b.Enabled=true;
                await Call(form,"InitializePane","home",panes["home"]);File.AppendAllText(Path.Combine(root,"test-progress.txt"),"; home ready");await Call(form,"ActivatePane","home",null);await Loaded(panes["home"]);
                Check(await panes["home"].CoreWebView2.ExecuteScriptAsync("getComputedStyle(document.querySelector('.aos-primary-nav')).display")=="\"none\"","Duplicate webpage navigation visible");
                await panes["home"].CoreWebView2.ExecuteScriptAsync("document.getElementById('draft').value='home draft';document.getElementById('wiki').click()");
                for(int i=0;i<100 && (string)Get(form,"activePane")!="github-wiki";i++)await Task.Delay(100);
                Check((string)Get(form,"activePane")=="github-wiki","Wiki new-window link not routed inside");await Loaded(panes["github-wiki"]);
                await panes["github-wiki"].CoreWebView2.ExecuteScriptAsync("document.getElementById('draft').value='wiki draft';window.scrollTo(0,600);window.testState=17");
                buttons["home"].PerformClick();await Task.Delay(150);
                Check(await panes["home"].CoreWebView2.ExecuteScriptAsync("document.getElementById('draft').value")=="\"home draft\"","Home draft lost");
                await panes["home"].CoreWebView2.ExecuteScriptAsync("document.getElementById('reader').click()");
                for(int i=0;i<100 && (string)Get(form,"activePane")!="paper";i++)await Task.Delay(100);
                Check((string)Get(form,"activePane")=="paper","Reader link not routed inside");await Loaded(panes["paper"]);
                await panes["paper"].CoreWebView2.ExecuteScriptAsync("document.getElementById('draft').value='reader draft'");
                buttons["github-wiki"].PerformClick();await Task.Delay(150);
                Check(await panes["github-wiki"].CoreWebView2.ExecuteScriptAsync("window.testState===17 && document.getElementById('draft').value==='wiki draft' && scrollY===600")=="true","Wiki state/scroll lost");
                buttons["paper"].PerformClick();await Task.Delay(150);
                Check(await panes["paper"].CoreWebView2.ExecuteScriptAsync("document.getElementById('draft').value")=="\"reader draft\"","Reader draft lost");
                buttons["home"].PerformClick();await Task.Delay(150);await panes["home"].CoreWebView2.ExecuteScriptAsync("document.getElementById('assistant').click()");
                for(int i=0;i<100 && (string)Get(form,"activePane")!="assistant";i++)await Task.Delay(100);
                Check((string)Get(form,"activePane")=="assistant","Host assistant link not routed");await Loaded(panes["assistant"]);
                var method=form.GetType().GetMethod("PaneFor",flags);
                Check(method.Invoke(form,new object[]{"https://outside.invalid/","home"})==null,"External origin treated as app");
                Check(method.Invoke(form,new object[]{"http://workbench.test.evil.invalid/","home"})==null,"Origin prefix bypass");
                await panes["assistant"].CoreWebView2.ExecuteScriptAsync("document.getElementById('draft').value='source draft';scrollTo(0,450);window.ticks=0;setInterval(()=>window.ticks++,50);window.chrome.webview.postMessage(JSON.stringify({type:'open-citation',url:'http://wiki.test/?citation=one'}))");
                for(int i=0;i<100 && (string)Get(form,"activePane")!="citation";i++)await Task.Delay(100);
                Check((string)Get(form,"activePane")=="citation","Citation message not handled");await Loaded(panes["citation"]);
                var back=(Button)Get(form,"returnConversation");Check(back.Visible,"Return button missing");
                Check((string)Get(form,"citationOrigin")=="assistant","Wrong return origin");
                await panes["citation"].CoreWebView2.ExecuteScriptAsync("document.getElementById('reader').click()");await Task.Delay(300);
                Check((string)Get(form,"activePane")=="citation","Nested citation replaced ordinary product pane");
                Check((string)Get(form,"citationOrigin")=="assistant","Nested link lost original conversation");
                back.PerformClick();await Task.Delay(200);
                Check((string)Get(form,"activePane")=="assistant","Return did not restore source pane");
                Check(await panes["assistant"].CoreWebView2.ExecuteScriptAsync("document.getElementById('draft').value==='source draft' && scrollY===450 && ticks>2")=="true","Source draft/scroll/background updates lost");
                buttons["paper"].PerformClick();await Task.Delay(150);
                await Call(form,"OpenCitation","paper","http://reader.test/?same-product=1");await Loaded(panes["citation"]);back.PerformClick();await Task.Delay(150);
                Check(await panes["paper"].CoreWebView2.ExecuteScriptAsync("document.getElementById('draft').value==='reader draft'")=="true","Same-product citation overwrote Reader");
                await Call(form,"OpenCitation","paper","http://reader.test/fail");await Task.Delay(1000);Check(await panes["citation"].CoreWebView2.ExecuteScriptAsync("!!document.getElementById('fixture-failure')")=="true","Failure fixture not displayed");Check(back.Visible,"Failed navigation hid return button");back.PerformClick();await Task.Delay(150);
                Check((string)Get(form,"activePane")=="paper","Failed page return did not restore Reader");
                File.WriteAllText(Path.Combine(root,"citation-result.txt"),"PASS: citation return (nested links, same-product, failure, source draft/scroll/background updates) and native WebView Wiki/Reader internal routing, retained drafts/scroll/JS state, host assistant switching, duplicate rail hidden, origin checks.");
            } catch(Exception ex) {File.WriteAllText(Path.Combine(root,"citation-result.txt"),"FAIL: "+ex);Environment.ExitCode=1;}
            finally {form.Close();}
        };
        Application.Run(form);
    }
}
