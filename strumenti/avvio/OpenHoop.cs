// OpenHoop.exe - avvio di OpenHoop per Windows (GPL-3.0, parte di OpenHoop).
// Doppio clic: se manca l'installazione la avvia; altrimenti avvia il server Python senza finestra, apre il
// browser e resta come icona vicino all'orologio (Apri / Esci). Se OpenHoop e' gia' aperto apre solo il browser.
// Compilare con:  strumenti\avvio\compila.bat   (usa il compilatore C# incluso in Windows, .NET Framework 4)
using System;
using System.Diagnostics;
using System.Drawing;
using System.Globalization;
using System.IO;
using System.Net;
using System.Text;
using System.Threading;
using System.Windows.Forms;

static class OpenHoop
{
    static string Radice = AppDomain.CurrentDomain.BaseDirectory;
    static int Porta = 8766;
    static string Url { get { return "http://127.0.0.1:" + Porta + "/"; } }
    static string Lingua = CultureInfo.CurrentUICulture.TwoLetterISOLanguageName;

    static string T(string it, string en, string fr, string es)
    {
        switch (Lingua) { case "it": return it; case "fr": return fr; case "es": return es; default: return en; }
    }

    static bool Risponde()
    {
        try
        {
            var r = (HttpWebRequest)WebRequest.Create(Url + "api/setup");
            r.Timeout = 1500;
            using (var x = (HttpWebResponse)r.GetResponse()) return x.StatusCode == HttpStatusCode.OK;
        }
        catch { return false; }
    }

    static void Apri() { try { Process.Start(Url); } catch { } }

    [STAThread]
    static void Main()
    {
        string p = Environment.GetEnvironmentVariable("PORTA_CATALOGO");
        int n;
        if (!string.IsNullOrEmpty(p) && int.TryParse(p, out n)) Porta = n;

        bool nuovo;
        using (var m = new Mutex(true, "OpenHoop-avvio-" + Porta, out nuovo))
        {
            if (!nuovo || Risponde()) { Apri(); return; }

            string py = Path.Combine(Radice, @".venv\Scripts\python.exe");
            string server = Path.Combine(Radice, @"02_script\catalogo_server.py");
            if (!File.Exists(py))
            {
                var ok = MessageBox.Show(T("OpenHoop non è ancora installato.\n\nAvvio l'installazione? Ci vogliono 15-40 minuti e una connessione a Internet (circa 8 GB).",
                                           "OpenHoop is not installed yet.\n\nStart the setup now? It takes 15-40 minutes and an Internet connection (about 8 GB).",
                                           "OpenHoop n'est pas encore installé.\n\nLancer l'installation ? Comptez 15 à 40 minutes et une connexion Internet (environ 8 Go).",
                                           "OpenHoop aún no está instalado.\n\n¿Iniciar la instalación? Tarda 15-40 minutos y necesita conexión a Internet (unos 8 GB)."),
                                         "OpenHoop", MessageBoxButtons.OKCancel, MessageBoxIcon.Information);
                if (ok != DialogResult.OK) return;
                var inst = Process.Start(new ProcessStartInfo("powershell.exe",
                    "-NoProfile -ExecutionPolicy Bypass -NoExit -File \"" + Path.Combine(Radice, @"strumenti\installa.ps1") + "\"")
                    { UseShellExecute = true, WorkingDirectory = Radice });
                inst.WaitForExit();
                if (!File.Exists(py)) return;
            }

            // server senza finestra; il registro va in %APPDATA%\OpenHoop\openhoop.log (utile per le segnalazioni)
            string cart = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.ApplicationData), "OpenHoop");
            Directory.CreateDirectory(cart);
            // il registro dell'avvio precedente resta in openhoop.prev.log (prima veniva sovrascritto e dopo un
            // riavvio non si capiva più cosa era successo)
            string fLog = Path.Combine(cart, "openhoop.log"), fPrec = Path.Combine(cart, "openhoop.prev.log");
            try { if (File.Exists(fLog)) { if (File.Exists(fPrec)) File.Delete(fPrec); File.Move(fLog, fPrec); } } catch { }
            var log = new StreamWriter(fLog, false, Encoding.UTF8) { AutoFlush = true };
            log.WriteLine("=== avvio " + DateTime.Now.ToString("yyyy-MM-dd HH:mm:ss") + " ===");
            var si = new ProcessStartInfo(py, "-u \"" + server + "\" --no-browser")
            {
                UseShellExecute = false, CreateNoWindow = true, WorkingDirectory = Radice,
                RedirectStandardOutput = true, RedirectStandardError = true,
                StandardOutputEncoding = Encoding.UTF8, StandardErrorEncoding = Encoding.UTF8
            };
            si.EnvironmentVariables["PYTHONIOENCODING"] = "utf-8";
            si.EnvironmentVariables["PORTA_CATALOGO"] = Porta.ToString();
            var proc = Process.Start(si);
            proc.OutputDataReceived += (s, e) => { if (e.Data != null) lock (log) log.WriteLine(e.Data); };
            proc.ErrorDataReceived += (s, e) => { if (e.Data != null) lock (log) log.WriteLine(e.Data); };
            proc.BeginOutputReadLine();
            proc.BeginErrorReadLine();

            Application.EnableVisualStyles();
            var icona = new NotifyIcon
            {
                Icon = Icon.ExtractAssociatedIcon(Application.ExecutablePath),
                Text = "OpenHoop", Visible = true
            };
            var menu = new ContextMenuStrip();
            menu.Items.Add(T("Apri OpenHoop", "Open OpenHoop", "Ouvrir OpenHoop", "Abrir OpenHoop"), null, (s, e) => Apri());
            menu.Items.Add(T("Esci", "Quit", "Quitter", "Salir"), null, (s, e) =>
            {
                try
                {
                    var r = (HttpWebRequest)WebRequest.Create(Url + "api/esci");
                    r.Method = "POST"; r.ContentType = "application/json"; r.ContentLength = 2; r.Timeout = 3000;
                    using (var w = r.GetRequestStream()) w.Write(new byte[] { 123, 125 }, 0, 2);
                    r.GetResponse().Close();
                }
                catch { }
                try { if (!proc.HasExited) proc.Kill(); } catch { }
                icona.Visible = false;
                Application.Exit();
            });
            icona.ContextMenuStrip = menu;
            icona.DoubleClick += (s, e) => Apri();

            // aspetta che il server risponda (il primo avvio carica i modelli), poi apre il browser
            var attesa = new System.Windows.Forms.Timer { Interval = 1000 };
            int secondi = 0;
            attesa.Tick += (s, e) =>
            {
                secondi++;
                if (Risponde())
                {
                    attesa.Stop();
                    Apri();
                    icona.ShowBalloonTip(4000, "OpenHoop", T("OpenHoop è aperto. Per chiuderlo: clic destro su questa icona → Esci.",
                        "OpenHoop is running. To close it: right-click this icon → Quit.",
                        "OpenHoop est ouvert. Pour le fermer : clic droit sur cette icône → Quitter.",
                        "OpenHoop está abierto. Para cerrarlo: clic derecho en este icono → Salir."), ToolTipIcon.Info);
                }
                else if (secondi > 120 && proc.HasExited)
                {
                    attesa.Stop();
                    MessageBox.Show(T("OpenHoop non è partito. Il registro è in:\n", "OpenHoop did not start. The log is in:\n",
                        "OpenHoop n'a pas démarré. Le journal se trouve ici :\n", "OpenHoop no ha arrancado. El registro está en:\n")
                        + Path.Combine(cart, "openhoop.log"), "OpenHoop", MessageBoxButtons.OK, MessageBoxIcon.Error);
                    icona.Visible = false;
                    Application.Exit();
                }
            };
            attesa.Start();
            Application.Run();
        }
    }
}
