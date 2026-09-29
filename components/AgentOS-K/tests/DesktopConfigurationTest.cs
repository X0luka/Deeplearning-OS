using System;
using System.Collections.Generic;
using System.IO;
using System.Web.Script.Serialization;

class DesktopConfigurationTest {
    static void Check(bool ok, string message) { if(!ok) throw new Exception(message); }
    static void Main(string[] args) {
        string file = Path.Combine(args[0], "synthetic-desktop.json");
        var serializer = new JavaScriptSerializer();
        var values = new Dictionary<string,string> {
            {"instance","dev-a"},{"distribution","Ubuntu"},{"user","tester"},
            {"repository","/home/tester/opensource/Deeplearning-OS/components/AgentOS-K"},
            {"host_port","18777"},{"wiki_port","18601"},{"paper_port","18602"}
        };
        Action save = () => File.WriteAllText(file,serializer.Serialize(values));
        save();var a=DesktopConfiguration.Load(file);
        values["instance"]="dev-b";save();var b=DesktopConfiguration.Load(file);
        Check(a.Identity!=b.Identity,"Mutex identity collision");
        Check(a.DataDirectory!=b.DataDirectory,"Data directory collision");
        Check(a.Title.Contains("dev-a"),"Missing test instance title");
        Check(a.WslArguments.Contains("--exec /usr/bin/env -i "),"Environment not cleared");
        Check(a.WslArguments.Contains("-m aos.desktop_instance --instance dev-a"),"Wrong entry point");
        Check(!a.DataDirectory.Contains(Path.DirectorySeparatorChar+"AgentOS"+Path.DirectorySeparatorChar),"Production state directory");
        int rejected=0;
        foreach(var change in new[] {
            new[]{"instance","../release"},new[]{"instance","dev-x\n"},new[]{"host_port","7777"},
            new[]{"wiki_port","18777"},new[]{"repository","/home/tester/code/AgentOS-K"},
            new[]{"repository","/home/tester/../copy/components/AgentOS-K"},
            new[]{"distribution","Ubuntu --exec evil"},new[]{"user","a b"}
        }) {
            string old=values[change[0]];values[change[0]]=change[1];save();
            try {DesktopConfiguration.Load(file);throw new Exception("Invalid config accepted: "+change[0]);}
            catch(ArgumentException) {rejected++;}
            values[change[0]]=old;
        }
        File.WriteAllText(file,"{\"preparation_only\":\"DO_NOT_LAUNCH_BEFORE_ISOLATION_ACCEPTANCE\"}");
        try {DesktopConfiguration.Load(file);throw new Exception("Disabled config accepted");}
        catch(ArgumentException) {rejected++;}
        File.Delete(file);
        Console.WriteLine("PASS: instance identity/state separation, clean launch arguments, "+rejected+" invalid/disabled configurations rejected; no shell launched.");
    }
}
