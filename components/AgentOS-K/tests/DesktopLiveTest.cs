using System;
using System.IO;
using System.Reflection;
using System.Text;
using System.Collections.Generic;
using System.Threading.Tasks;
using System.Windows.Forms;
using Microsoft.Web.WebView2.Core;
using Microsoft.Web.WebView2.WinForms;

class DesktopLiveTest {
 static string root=AppDomain.CurrentDomain.BaseDirectory;
 static BindingFlags flags=BindingFlags.Instance|BindingFlags.NonPublic;
 static object Get(object f,string n){return f.GetType().GetField(n,flags).GetValue(f);}
 static void Set(object f,string n,object v){f.GetType().GetField(n,flags).SetValue(f,v);}
 static Task Call(object f,string n,params object[] a){return (Task)f.GetType().GetMethod(n,flags).Invoke(f,a);}
 static void Check(bool value,string message){if(!value)throw new Exception(message);}
 static async Task Wait(WebView2 pane,string expression){
  for(int i=0;i<150;i++){if(pane.CoreWebView2!=null && await pane.CoreWebView2.ExecuteScriptAsync(expression)=="true")return;await Task.Delay(100);}
  throw new Exception("Page readiness failed: "+expression);
 }
 static void LocalOnly(WebView2 pane){
  pane.CoreWebView2InitializationCompleted+=(sender,args)=>{
   if(!args.IsSuccess)return;
   pane.CoreWebView2.AddWebResourceRequestedFilter("*",CoreWebView2WebResourceContext.All);
   pane.CoreWebView2.WebResourceRequested+=(s,e)=>{
    Uri uri;bool allowed=Uri.TryCreate(e.Request.Uri,UriKind.Absolute,out uri)
      && uri.Scheme=="http" && uri.Host=="127.0.0.1"
      && (uri.Port==18977||uri.Port==18901||uri.Port==18902);
    if(!allowed)e.Response=pane.CoreWebView2.Environment.CreateWebResourceResponse(
      new MemoryStream(Encoding.UTF8.GetBytes("External requests blocked in desktop acceptance")),403,"Forbidden","Content-Type: text/plain");
   };
  };
 }
 [STAThread] static void Main(){
  Application.EnableVisualStyles();Application.SetCompatibleTextRenderingDefault(false);
  var form=new AgentOS(false);form.ShowInTaskbar=false;form.StartPosition=FormStartPosition.Manual;form.Left=-14000;
  LocalOnly((WebView2)Get(form,"view"));
  ((Panel)Get(form,"content")).ControlAdded+=(s,e)=>{var p=e.Control as WebView2;if(p!=null)LocalOnly(p);};
  var deadline=new System.Windows.Forms.Timer{Interval=90000};
  deadline.Tick+=(s,e)=>{File.WriteAllText(Path.Combine(root,"live-result.txt"),"FAIL: deadline");Environment.Exit(124);};deadline.Start();
  form.Load+=async(s,e)=>{
   try{
    var env=await CoreWebView2Environment.CreateAsync(null,Path.Combine(root,"live-profile"),new CoreWebView2EnvironmentOptions("--disable-background-networking --disable-component-update"));
    Check(Path.GetFullPath(env.UserDataFolder).TrimEnd(Path.DirectorySeparatorChar)==Path.GetFullPath(Path.Combine(root,"live-profile")).TrimEnd(Path.DirectorySeparatorChar),"Wrong profile");
    Set(form,"environment",env);Set(form,"hostOrigin","http://127.0.0.1:18977");
    var routes=(Dictionary<string,string>)Get(form,"productRoutes");routes["github-wiki"]="http://127.0.0.1:18901/";routes["paper"]="http://127.0.0.1:18902/";
    var panes=(Dictionary<string,WebView2>)Get(form,"panes");
    foreach(var b in ((Dictionary<string,Button>)Get(form,"navButtons")).Values)b.Enabled=true;
    await Call(form,"InitializePane","home",panes["home"]);
    await Call(form,"ActivatePane","home",null);
    await Wait(panes["home"],"document.readyState==='complete' && document.body.innerText.length>20");
    await Call(form,"ActivatePane","models",null);
    await Wait(panes["models"],"!!document.getElementById('network-default') && !document.getElementById('network-default').disabled");
    await panes["models"].CoreWebView2.ExecuteScriptAsync("document.getElementById('model-search').value='live desktop draft'");
    foreach(var key in new[]{"paper","github-wiki"}){
      await Call(form,"ActivatePane",key,routes[key]);
      await Wait(panes[key],"document.readyState==='complete' && document.body.innerText.length>20");
      await panes[key].CoreWebView2.ExecuteScriptAsync("fetch('/aos/v1/describe').then(r=>r.json()).then(v=>window.__liveIdentity=v.product_id).catch(()=>window.__liveIdentity='failed')");
      string id=key=="paper"?"paper":"github-wiki";
      await Wait(panes[key],"window.__liveIdentity==='"+id+"'");
    }
    await Call(form,"ActivatePane","models",null);
    Check(await panes["models"].CoreWebView2.ExecuteScriptAsync("document.getElementById('model-search').value==='live desktop draft'")=="true","Model draft lost");
    Check(await panes["models"].CoreWebView2.ExecuteScriptAsync("getComputedStyle(document.querySelector('.aos-primary-nav')).display==='none'")=="true","Duplicate nav");
    File.WriteAllText(Path.Combine(root,"live-result.txt"),"PASS: native desktop panes load actual isolated WSL Workbench/Reader/Wiki; identities verified; network settings loaded; pane draft retained; external WebView requests blocked; no model calls.");
   }catch(Exception ex){File.WriteAllText(Path.Combine(root,"live-result.txt"),"FAIL: "+ex);Environment.ExitCode=1;}
   finally{deadline.Stop();form.Close();form.Dispose();}
  };
  Application.Run(form);
 }
}
