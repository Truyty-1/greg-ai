import os
import re
import json
import time
import inspect
import threading
import subprocess
import queue
import urllib.request
import urllib.error
from typing import Callable, Dict, Any, Tuple, Optional, List, Union

from rich.console import Console

console = Console()


class SkillRegistry:
    """Registre central gérant la découverte, l'auto-importation, l'intégration
    au prompt système et l'exécution automatique des skills.
    """

    def __init__(self):
        self._skills: Dict[str, Dict[str, Any]] = {}

    def register(self, name: Optional[str] = None, description: Optional[str] = None):
        """Décorateur pour enregistrer automatiquement une fonction Python comme Skill/Outil."""
        def decorator(func: Callable):
            skill_name = name or func.__name__
            skill_doc = description or (inspect.getdoc(func) or "Aucune description fournie.")

            sig = inspect.signature(func)
            params = {}
            for param_name, param in sig.parameters.items():
                param_type = param.annotation.__name__ if param.annotation != inspect.Parameter.empty else "string"
                params[param_name] = param_type

            self._skills[skill_name] = {
                "name": skill_name,
                "description": skill_doc,
                "function": func,
                "parameters": params,
            }
            console.print(f"[dim]🧩 [SKILLS] Skill enregistré : '{skill_name}' (paramètres : {params})[/dim]")
            return func
        return decorator

    def get_all_skills(self) -> Dict[str, Dict[str, Any]]:
        return self._skills

    def get_prompt_description(self) -> str:
        if not self._skills:
            return "Aucun skill externe n'est disponible actuellement."

        prompt_lines = [
            "Voici la liste des outils (skills) disponibles sur le système :",
        ]
        for name, info in self._skills.items():
            params_str = ", ".join([f'"{p}": <{t}>' for p, t in info["parameters"].items()])
            prompt_lines.append(f"- Outil : {name}")
            prompt_lines.append(f"  Description : {info['description']}")
            prompt_lines.append(f"  Format d'appel JSON : {{\n    \"action\": \"use_skill\",\n    \"skill\": \"{name}\",\n    \"params\": {{{params_str}}}\n  }}")

        prompt_lines.append(
            "\nIMPORTANT SUR LE FORMAT JSON ET MULTI-SKILLS :"
            "\n- Tu peux appeler plusieurs actions/skills dans un seul message en fournissant une liste JSON : "
            "[{\"action\": \"use_skill\", \"skill\": \"skill1\", \"params\": {...}}, {\"action\": \"use_skill\", \"skill\": \"skill2\", \"params\": {...}}]."
            "\n- Les valeurs texte contenant des retours à la ligne peuvent être écrites directement ou avec \\n."
        )
        return "\n".join(prompt_lines)

    def execute(self, skill_name: str, **kwargs) -> Any:
        skill_name = (skill_name or "").strip()

        if skill_name not in self._skills:
            msg = (f"Erreur : Le skill '{skill_name}' n'existe pas. "
                   f"Skills disponibles : {list(self._skills.keys())}")
            console.print(f"[bold red]❌ [SKILL] {msg}[/bold red]")
            return msg

        console.print(f"[bold blue]▶️  [SKILL] Exécution de '{skill_name}' avec paramètres : {kwargs}[/bold blue]")
        start_time = time.time()
        try:
            result = self._skills[skill_name]["function"](**kwargs)
            elapsed = time.time() - start_time
            preview = str(result)
            if len(preview) > 400:
                preview = preview[:400] + " ...(tronqué pour l'affichage console)"
            console.print(
                f"[bold green]✅ [SKILL] '{skill_name}' exécuté avec succès en {elapsed:.2f}s.[/bold green]\n"
                f"[bold green]   Résultat : {preview}[/bold green]"
            )
            return result
        except TypeError as e:
            elapsed = time.time() - start_time
            msg = (f"Erreur d'appel du skill '{skill_name}' après {elapsed:.2f}s : paramètres invalides ({e}). "
                   f"Paramètres attendus : {self._skills[skill_name]['parameters']}")
            console.print(f"[bold red]❌ [SKILL] {msg}[/bold red]")
            return msg
        except Exception as e:
            elapsed = time.time() - start_time
            console.print(f"[bold red]❌ [SKILL] Erreur lors de l'exécution de '{skill_name}' après {elapsed:.2f}s : {e}[/bold red]")
            console.print_exception(show_locals=False)
            return f"Erreur lors de l'exécution du skill '{skill_name}' : {str(e)}"

    @staticmethod
    def find_skill_json_spans(text: str) -> List[Tuple[int, int]]:
        if not text:
            return []

        spans = []
        pos = 0
        while pos < len(text):
            anchor = text.find('"action"', pos)
            if anchor == -1:
                anchor = text.find("use_skill", pos)
            if anchor == -1:
                break

            start_obj = text.rfind("{", pos, anchor + 10)
            start_arr = text.rfind("[", pos, anchor + 10)
            
            if start_obj == -1 and start_arr == -1:
                start_obj = text.find("{", anchor)
                start_arr = text.find("[", anchor)

            if start_obj == -1 and start_arr == -1:
                pos = anchor + 1
                continue

            if start_arr != -1 and (start_obj == -1 or start_arr < start_obj):
                start = start_arr
                open_char = "["
                close_char = "]"
            else:
                start = start_obj
                open_char = "{"
                close_char = "}"

            depth = 0
            in_string = False
            escape = False
            found_end = -1

            for i in range(start, len(text)):
                ch = text[i]
                if in_string:
                    if escape:
                        escape = False
                    elif ch == "\\":
                        escape = True
                    elif ch == '"':
                        in_string = False
                else:
                    if ch == '"':
                        in_string = True
                    elif ch == "{" or ch == "[":
                        depth += 1
                    elif ch == "}" or ch == "]":
                        depth -= 1
                        if depth == 0:
                            found_end = i + 1
                            break

            if found_end != -1:
                spans.append((start, found_end))
                pos = found_end
            else:
                # ROBUSTesse anti-troncature : si le JSON est coupé par la réponse, on prend jusqu'à la fin du texte
                spans.append((start, len(text)))
                break

        return spans

    @staticmethod
    def _light_json_repair(raw: str) -> str:
        repaired = re.sub(r",\s*([}\]])", r"\1", raw)
        repaired = (repaired
                    .replace("\u201c", '"').replace("\u201d", '"')
                    .replace("\u2018", "'").replace("\u2019", "'"))
        # Fermeture automatique des accolades et crochets non fermés (troncature)
        open_braces = repaired.count('{') - repaired.count('}')
        open_brackets = repaired.count('[') - repaired.count(']')
        if open_braces > 0:
            repaired += '}' * open_braces
        if open_brackets > 0:
            repaired += ']' * open_brackets
        return repaired

    def parse_and_execute(self, llm_response: str) -> Tuple[bool, Optional[str], Optional[Any]]:
        if not llm_response or ("use_skill" not in llm_response and '"action"' not in llm_response):
            return False, None, None

        spans = self.find_skill_json_spans(llm_response)
        if not spans:
            return False, None, None

        executed_skills = []
        results = []

        for start, end in spans:
            raw_json = llm_response[start:end]
            data = None
            try:
                data = json.loads(raw_json, strict=False)
            except Exception:
                try:
                    data = json.loads(self._light_json_repair(raw_json), strict=False)
                except Exception:
                    continue

            actions = data if isinstance(data, list) else [data]

            for item in actions:
                if isinstance(item, dict) and item.get("action") == "use_skill" and "skill" in item:
                    skill_name = str(item["skill"]).strip()
                    params = item.get("params", {}) or {}
                    if not isinstance(params, dict):
                        params = {}

                    res = self.execute(skill_name, **params)
                    executed_skills.append(skill_name)
                    results.append(f"--- Résultat pour {skill_name} ---\n{res}")

        if executed_skills:
            combined_skills = ", ".join(executed_skills)
            combined_results = "\n\n".join(results)
            return True, combined_skills, combined_results

        return False, None, None


try:
    registry
except NameError:
    registry = SkillRegistry()

skill = registry.register


def clean_text_for_speech(text: str) -> str:
    if not text:
        return ""

    if "[SILENCE]" in text:
        return ""

    spans = registry.find_skill_json_spans(text)
    if spans:
        cleaned = text
        for start, end in reversed(spans):
            cleaned = cleaned[:start] + " " + cleaned[end:]
        text = cleaned

    text = re.sub(r"\[(?:début|fin)_[a-fA-F0-9]+\]", "", text)
    return text.strip()


class PersistentTerminal:
    def __init__(self):
        self.process = None
        self.output_queue = queue.Queue()
        self._lock = threading.Lock()
        self._reader_thread = None
        self._start()

    def _start(self):
        if os.name == "nt":
            shell_cmd = ["cmd.exe"]
        else:
            shell_cmd = ["/bin/bash"]

        self.process = subprocess.Popen(
            shell_cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
        )

        self._reader_thread = threading.Thread(target=self._read_output, daemon=True)
        self._reader_thread.start()

        if os.name == "nt":
            self.send_command("chcp 65001 >nul", wait_time=1.0)

    def _read_output(self):
        try:
            for line in iter(self.process.stdout.readline, ''):
                self.output_queue.put(line)
        except Exception:
            pass

    def send_command(self, command: str, wait_time: float = 3.0, max_total_wait: float = 20.0) -> str:
        with self._lock:
            if self.process is None or self.process.poll() is not None:
                self._start()

            marker = f"__GREG_END_{int(time.time() * 1000)}__"
            try:
                self.process.stdin.write(command + "\n")
                self.process.stdin.write(f"echo {marker}\n")
                self.process.stdin.flush()
            except Exception as e:
                return f"Impossible d'envoyer la commande au terminal : {e}"

            output_lines = []
            start = time.time()
            while time.time() - start < max_total_wait:
                try:
                    line = self.output_queue.get(timeout=wait_time)
                except queue.Empty:
                    break
                if marker in line:
                    break
                output_lines.append(line)

            return "".join(output_lines)

    def close(self):
        try:
            if self.process:
                self.process.terminate()
        except Exception:
            pass
        self.process = None


try:
    _terminal
except NameError:
    _terminal = None


@skill(description="Exécute une commande dans un terminal interactif PERSISTANT.")
def terminal_executer(commande: str, attente_secondes: float = 3.0) -> str:
    global _terminal
    try:
        if _terminal is None:
            _terminal = PersistentTerminal()
        output = _terminal.send_command(commande, wait_time=attente_secondes).strip()
        return f"Sortie du terminal :\n{output if output else '(aucune sortie)'}"
    except Exception as e:
        return f"Erreur terminal persistant : {str(e)}"


@skill(description="Redémarre le terminal persistant s'il est bloqué.")
def terminal_redemarrer() -> str:
    global _terminal
    try:
        if _terminal:
            _terminal.close()
        _terminal = PersistentTerminal()
        return "Terminal persistant redémarré avec succès."
    except Exception as e:
        return f"Erreur lors du redémarrage du terminal : {str(e)}"


@skill(description="Liste le contenu d'un dossier spécifié sur le PC.")
def lister_dossier(chemin: str = ".") -> str:
    try:
        fichiers = os.listdir(chemin)
        return f"Contenu de '{chemin}' : {', '.join(fichiers)}"
    except Exception as e:
        return f"Impossible de lire le dossier : {str(e)}"


@skill(description="Exécute UNE commande terminal isolée.")
def executer_commande(commande: str) -> str:
    try:
        res = subprocess.run(commande, shell=True, capture_output=True, text=True, timeout=30)
        output = res.stdout if res.stdout else res.stderr
        return f"Résultat de la commande :\n{output.strip()}"
    except Exception as e:
        return f"Erreur lors de l'exécution : {str(e)}"


@skill(description="Exécute une commande terminal en tant qu'administrateur.")
def executer_commande_admin(commande: str) -> str:
    try:
        ps_command = f"Start-Process cmd.exe -ArgumentList '/c {commande} > \"\$env:TEMP\\cmd_out.txt\" 2>&1' -Verb RunAs -Wait"
        subprocess.run(["powershell", "-Command", ps_command], capture_output=True, text=True, timeout=60)

        output_file = os.path.join(os.environ.get("TEMP", "C:\\Temp"), "cmd_out.txt")
        if os.path.exists(output_file):
            with open(output_file, "r", encoding="utf-8", errors="ignore") as f:
                output = f.read()
            os.remove(output_file)
            return f"Résultat de la commande Admin :\n{output.strip()}"
        return "Commande admin exécutée, mais aucun fichier de sortie récupéré."
    except Exception as e:
        return f"Erreur lors de l'exécution admin : {str(e)}"


@skill(description="Effectue une requête HTTP (GET ou POST) vers une URL web.")
def faire_requete_web(url: str, methode: str = "GET", donnees: Optional[str] = None) -> str:
    try:
        data_bytes = donnees.encode("utf-8") if donnees else None
        req = urllib.request.Request(url, data=data_bytes, method=methode.upper())
        with urllib.request.urlopen(req, timeout=15) as response:
            body = response.read().decode("utf-8", errors="ignore")
            return f"Statut : {response.status}\nRéponse :\n{body[:2000]}"
    except Exception as e:
        return f"Erreur lors de la requête web : {str(e)}"


@skill(description="Trouve et retourne le chemin absolu d'un dossier spécial du PC (desktop, documents, downloads, etc.).")
def trouver_chemin_special(nom_dossier: str) -> str:
    nom = nom_dossier.lower().strip()
    home = os.path.expanduser("~")

    chemins_connus = {
        "desktop": os.path.join(home, "Desktop"),
        "bureau": os.path.join(home, "Desktop"),
        "documents": os.path.join(home, "Documents"),
        "downloads": os.path.join(home, "Downloads"),
        "telechargements": os.path.join(home, "Downloads"),
        "home": home,
        "profil": home,
        "temp": os.environ.get("TEMP", os.path.join(home, "AppData", "Local", "Temp"))
    }

    if nom in chemins_connus:
        path = chemins_connus[nom]
        if os.path.exists(path):
            return f"Chemin trouvé pour '{nom}' : {path}"
        else:
            return f"Le dossier '{nom}' existe dans la théorie ({path}), mais est introuvable."

    for key, path in chemins_connus.items():
        if key in nom or nom in key:
            return f"Chemin suggéré pour '{nom}' ({key}) : {path}"

    return f"Impossible de résoudre automatiquement le chemin pour '{nom}'."


@skill(description="Crée (ou écrase) un fichier texte avec le contenu fourni à l'emplacement indiqué.")
def creer_fichier(chemin: str, contenu: str = "") -> str:
    try:
        if not chemin or not chemin.strip():
            raise ValueError("Le paramètre 'chemin' est vide.")

        chemin_nettoye = chemin.strip().strip('"').strip("'")
        chemin_nettoye = os.path.expanduser(os.path.expandvars(chemin_nettoye))

        parent = os.path.dirname(chemin_nettoye)
        if parent and not os.path.exists(parent):
            os.makedirs(parent, exist_ok=True)

        with open(chemin_nettoye, "w", encoding="utf-8") as f:
            f.write(contenu)

        taille = os.path.getsize(chemin_nettoye)
        chemin_absolu = os.path.abspath(chemin_nettoye)
        return f"Fichier '{chemin_absolu}' créé avec succès ({taille} octets)."
    except Exception as e:
        return f"Erreur lors de la création du fichier : {str(e)}"


@skill(description="Lance une application sur le PC en recherchant son nom sur le bureau, dans le menu Démarrer ou via son exécutable.")
def lancer_application(nom_app: str) -> str:
    nom = nom_app.strip().lower()
    
    desktop = os.path.join(os.path.expanduser("~"), "Desktop")
    public_desktop = "C:\\Users\\Public\\Desktop"
    
    paths_to_search = [desktop, public_desktop]
    
    appdata_programs = os.path.join(os.path.expanduser("~"), "AppData", "Roaming", "Microsoft", "Windows", "Start Menu", "Programs")
    program_data = "C:\\ProgramData\\Microsoft\\Windows\\Start Menu\\Programs"
    paths_to_search.extend([appdata_programs, program_data])
    
    found_path = None
    
    for search_dir in paths_to_search:
        if os.path.exists(search_dir):
            for root, dirs, files in os.walk(search_dir):
                for f in files:
                    if nom in f.lower() and (f.endswith(".lnk") or f.endswith(".exe") or f.endswith(".url")):
                        found_path = os.path.join(root, f)
                        break
                if found_path:
                    break
        if found_path:
            break
            
    if found_path:
        try:
            os.startfile(found_path)
            return f"Application '{nom_app}' lancée avec succès via {found_path}."
        except Exception as e:
            return f"Raccourci trouvé à {found_path}, mais échec du lancement : {e}"
            
    try:
        os.startfile(nom)
        return f"Application '{nom_app}' lancée directement."
    except Exception:
        pass
        
    try:
        subprocess.run(f"start {nom}", shell=True, capture_output=True, text=True)
        return f"Tentative de lancement de '{nom_app}' via le système."
    except Exception as e:
        return f"Impossible de trouver ou de lancer l'application '{nom_app}' : {str(e)}"


# =====================================================================
# NOUVEAUX SKILLS DEMANDÉS
# =====================================================================

@skill(description="Récupère tout le contenu sous forme de texte de plusieurs fichiers ou dossiers spécifiés (passés séparés par virgule ou dans une liste).")
def lire_contenu_fichiers_ou_dossiers(chemins: str) -> str:
    """Récupère le texte de tous les fichiers/dossiers donnés (séparés par virgule)."""
    liste_chemins = [c.strip().strip('"').strip("'") for c in chemins.split(",") if c.strip()]
    
    resultats = []
    
    for c in liste_chemins:
        c_exp = os.path.expanduser(os.path.expandvars(c))
        if not os.path.exists(c_exp):
            resultats.append(f"❌ '{c}' : Chemin introuvable.")
            continue
            
        if os.path.isfile(c_exp):
            try:
                with open(c_exp, "r", encoding="utf-8", errors="replace") as f:
                    contenu = f.read()
                resultats.append(f"📄 FICHIER : {c_exp}\n---\n{contenu}\n---")
            except Exception as e:
                resultats.append(f"❌ Erreur lecture fichier '{c_exp}' : {e}")
                
        elif os.path.isdir(c_exp):
            contenu_dossier = [f"📂 DOSSIER : {c_exp}"]
            fichiers_lus = 0
            for root, _, files in os.walk(c_exp):
                for file_name in files:
                    file_path = os.path.join(root, file_name)
                    ext = os.path.splitext(file_name)[1].lower()
                    if ext in [".txt", ".py", ".json", ".md", ".js", ".html", ".css", ".cpp", ".h", ".c", ".java", ".xml", ".yml", ".yaml", ".log", ".bat", ".ps1", ".sh", ""]:
                        try:
                            with open(file_path, "r", encoding="utf-8", errors="replace") as f:
                                txt = f.read()
                            contenu_dossier.append(f"\n--- Fichier : {file_path} ---\n{txt}")
                            fichiers_lus += 1
                        except Exception as e:
                            contenu_dossier.append(f"\n❌ Erreur lecture '{file_path}' : {e}")
            if fichiers_lus == 0:
                contenu_dossier.append("Aucun fichier texte lisible trouvé dans le dossier.")
            resultats.append("\n".join(contenu_dossier))

    return "\n\n====================\n\n".join(resultats)


@skill(description="Identifie la fenêtre actuellement active au premier plan et retourne tous les explorateurs de fichiers/dossiers ouverts sur le PC.")
def obtenir_explorateurs_ouverts() -> str:
    """Retourne la liste des fenêtres d'explorateur ouvertes ainsi que la fenêtre actuellement active au premier plan."""
    import pygetwindow as gw
    
    fenetres_actives = gw.getActiveWindow()
    titre_actif = fenetres_actives.title if fenetres_actives else "Aucune"
    
    toutes_fenetres = gw.getAllTitles()
    explorateurs = []
    
    if os.name == "nt":
        try:
            import win32com.client
            shell = win32com.client.Dispatch("Shell.Application")
            for window in shell.Windows():
                try:
                    name = window.Name
                    if "Explorer" in name or "Explorateur" in name:
                        path = window.LocationURL
                        titre = window.LocationName
                        explorateurs.append(f"- '{titre}' -> {path}")
                except Exception:
                    pass
        except Exception:
            pass

    if not explorateurs:
        for t in toutes_fenetres:
            if t.strip() and ("Explorateur" in t or "Explorer" in t or "\\" in t or ":" in t):
                explorateurs.append(f"- Fenêtre : '{t}'")

    res = [f"🖥️ Fenêtre actuellement active au premier plan : '{titre_actif}'\n"]
    if explorateurs:
        res.append("📂 Fenêtres d'explorateur ouvertes détectées :")
        res.extend(explorateurs)
    else:
        res.append("📂 Aucun explorateur de fichiers spécifique détecté parmi les fenêtres ouvertes.")

    return "\n".join(res)