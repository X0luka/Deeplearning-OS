using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Text.RegularExpressions;
using System.Web.Script.Serialization;

internal sealed class DesktopConfiguration {
    public string Instance, Distribution, User, Repository;
    public int HostPort, WikiPort, PaperPort;
    public string Title { get { return "Deeplearning-OS · 开发测试 [" + Instance + "]"; } }
    public string Identity { get { return "Local\\\\Deeplearning-OS.Development." + Environment.UserName + "." + Instance; } }
    public string DataDirectory {
        get {
            string path = Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData);
            foreach (string segment in new[] {"Deeplearning-OS-Development", Instance}) {
                path = Path.Combine(path, segment);
                if ((Directory.Exists(path) || File.Exists(path)) && (File.GetAttributes(path) & FileAttributes.ReparsePoint) != 0)
                    throw new ArgumentException("测试数据目录不能是目录链接。");
            }
            return path;
        }
    }
    static string Field(Dictionary<string,string> values, string name, string pattern) {
        string value;
        if (!values.TryGetValue(name, out value) || value == null || !Regex.IsMatch(value, pattern))
            throw new ArgumentException("缺少或无效的桌面配置：" + name);
        return value;
    }
    static int Port(Dictionary<string,string> values, string name) {
        int port;
        string value = Field(values, name, @"\A[0-9]{4,5}\z");
        if (!int.TryParse(value, out port) || port < 1024 || port > 65535 || new[] {7777,8601,8602,8777,8701,8702}.Contains(port))
            throw new ArgumentException("测试端口无效或与正式端口冲突：" + name);
        return port;
    }
    public static DesktopConfiguration Load(string file) {
        var values = new JavaScriptSerializer().Deserialize<Dictionary<string,string>>(File.ReadAllText(file));
        if (values == null || values.Keys.Except(new[] {"instance","distribution","user","repository","host_port","wiki_port","paper_port"}).Any())
            throw new ArgumentException("此桌面配置尚未启用或包含未知字段。");
        var result = new DesktopConfiguration {
            Instance = Field(values,"instance",@"\Adev-[a-z0-9][a-z0-9-]{0,35}\z"),
            Distribution = Field(values,"distribution",@"\A[A-Za-z0-9][A-Za-z0-9_.-]*\z"),
            User = Field(values,"user",@"\A[a-z_][a-z0-9_-]*\z"),
            Repository = Field(values,"repository",@"\A/[A-Za-z0-9_./-]+/components/AgentOS-K\z"),
            HostPort = Port(values,"host_port"), WikiPort = Port(values,"wiki_port"), PaperPort = Port(values,"paper_port")
        };
        if (result.Repository.Split('/').Any(s => s == "." || s == "..") || result.Repository.Contains("//"))
            throw new ArgumentException("仓库路径必须规范化。");
        if (new[] {result.HostPort,result.WikiPort,result.PaperPort}.Distinct().Count() != 3)
            throw new ArgumentException("三个测试端口必须不同。");
        string checkedPath = result.DataDirectory;
        return result;
    }
    public string WslArguments {
        get {
            return "-d " + Distribution + " -u " + User + " --cd " + Repository +
                " --exec /usr/bin/env -i PATH=/usr/bin:/bin LANG=C.UTF-8 PYTHONNOUSERSITE=1 PYTHONDONTWRITEBYTECODE=1 " +
                Repository + "/.venv/bin/python -m aos.desktop_instance --instance " + Instance +
                " --host-port " + HostPort + " --wiki-port " + WikiPort + " --paper-port " + PaperPort;
        }
    }
}
