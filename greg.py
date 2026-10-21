from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.keys import Keys
from rich.console import Console
import time
import re
import os
import sys
import importlib
import threading
import queue
import speech_recognition as sr
from pynput import mouse

from prompts import generate_enhanced_prompt
from voice_engine import PiperVoiceEngine
from clawd_bridge import ClawdBridge
from control_panel import ControlPanel
import skills as skills_module

console = Console()

class VoiceAssistant:
    def __init__(self, voice_engine):
        self.voice_engine = voice_engine
        self.recognizer = sr.Recognizer()
        self.microphone = sr.Microphone()

        self.left_click_pressed = False
        self.double_middle_clicked = False
        self._last_middle_click_time = 0
        self.manual_queue = queue.Queue()

        self.mouse_listener = mouse.Listener(
            on_click=self.on_click
        )
        self.mouse_listener.start()

        with self.microphone as source:
            console.print("[yellow]Ajustement rapide au bruit ambiant...[/yellow]")
            self.recognizer.adjust_for_ambient_noise(source, duration=0.5)
            console.print("[green]Microphone prêt ! (Dites 'OK Greg' pour poser une question, 'stop' pour interrompre).[/green]")

    def on_click(self, x, y, button, pressed):
        if button == mouse.Button.left:
            self.left_click_pressed = pressed
        elif button == mouse.Button.middle and pressed:
            now = time.time()
            if now - self._last_middle_click_time < 0.4:
                self.double_middle_clicked = True
                console.print("[bold cyan]🖱️ Double clic molette détecté ![/bold cyan]")
            self._last_middle_click_time = now

    def add_manual_command(self, text: str):
        if text:
            self.manual_queue.put(text)

    def listen_continuously(self, gui):
        while True:
            try:
                if not self.manual_queue.empty():
                    manual_text = self.manual_queue.get()
                    while not self.manual_queue.empty():
                        self.manual_queue.get()
                    console.print(f"[bold green]🗣️ Commande manuelle reçue :[/bold green] {manual_text}")
                    gui.update_display(f"Q: {manual_text}", state="triggered")
                    return manual_text

                self.voice_engine.stop_requested = False
                gui.update_display("En écoute (dites 'OK Greg'...)", state="listening")
                with self.microphone as source:
                    audio = self.recognizer.listen(source, timeout=1.0, phrase_time_limit=10)

                text = self.recognizer.recognize_google(audio, language="fr-FR")
                console.print(f"[dim]Entendu : {text}                [/dim]")

                text_lower = text.lower()

                if "stop" in text_lower:
                    self.voice_engine.stop_requested = True
                    console.print("[bold red]🛑 Ordre 'STOP' détecté ![/bold red]")
                    return "stop"

                if "ok" in text_lower or "greg" in text_lower:
                    after_ok = text_lower
                    for keyword in ["ok greg", "au québec", "ok gray", "au grec", "greg"]:
                        if keyword in after_ok:
                            after_ok = after_ok.split(keyword, 1)[1].strip()
                            break

                    after_ok = re.sub(r'^[,\s\-\.]+', '', after_ok)

                    if not after_ok or len(after_ok) < 2:
                        console.print("[bold yellow]👂 'OK Greg' détecté... Parlez (Arrêt auto après 1,5s de silence ou double clic molette).[/bold yellow]")
                        gui.update_display("Enregistrement en cours...", state="triggered")

                        full_sentence_parts = []
                        self.double_middle_clicked = False

                        while True:
                            try:
                                with self.microphone as source:
                                    audio_chunk = self.recognizer.listen(source, timeout=1.5, phrase_time_limit=10)
                                chunk_text = self.recognizer.recognize_google(audio_chunk, language="fr-FR")

                                if chunk_text:
                                    if "stop" in chunk_text.lower():
                                        self.voice_engine.stop_requested = True
                                        console.print("[bold red]🛑 Dictée interrompue par 'stop'.[/bold red]")
                                        return "stop"

                                    full_sentence_parts.append(chunk_text)
                                    console.print(f"[dim]Partie capturée : {chunk_text}[/dim]")
                            except sr.WaitTimeoutError:
                                break
                            except Exception:
                                pass

                            if self.double_middle_clicked:
                                console.print("[bold green]✅ Validation immédiate par double clic molette.[/bold green]")
                                break

                        if len(full_sentence_parts) > 0:
                            command = " ".join(full_sentence_parts)
                            console.print(f"[bold green]🗣️ Texte complet capturé :[/bold green] {command}")
                            gui.update_display(f"Q: {command}", state="triggered")
                            time.sleep(0.3)
                            return command
                    else:
                        command = after_ok
                        if "stop" in command.lower():
                            return "stop"
                        console.print(f"[bold green]🗣️ Déclenché via 'OK Greg' - Question :[/bold green] {command}")
                        gui.update_display(f"Q: {command}", state="triggered")
                        time.sleep(0.4)
                        return command
            except sr.WaitTimeoutError:
                pass
            except sr.UnknownValueError:
                pass
            except Exception:
                pass

class GeminiChat:
    def __init__(self):
        self.driver = None
        self.current_start_tag = ""
        self.current_end_tag = ""
        self.setup_driver()

    def setup_driver(self):
        chrome_options = Options()
        chrome_options.add_argument("--headless=new")
        chrome_options.add_argument("--window-size=1920,1080")

        profile_dir = os.path.join(os.getcwd(), "chrome_profile")
        chrome_options.add_argument(f"--user-data-dir={profile_dir}")

        chrome_options.add_argument("--disable-blink-features=AutomationControlled")
        chrome_options.add_experimental_option("excludeSwitches", ["enable-automation"])
        chrome_options.add_experimental_option('useAutomationExtension', False)
        chrome_options.add_argument("--disable-gpu")
        chrome_options.add_argument("--no-sandbox")
        chrome_options.add_argument("--disable-dev-shm-usage")

        self.driver = webdriver.Chrome(options=chrome_options)
        self.driver.execute_cdp_cmd('Page.addScriptToEvaluateOnNewDocument', {
            'source': '''
                Object.defineProperty(navigator, 'webdriver', {
                    get: () => undefined
                })
            '''
        })

    def open_gemini(self):
        console.print("[bold cyan]Opening Gemini Gem...[/bold cyan]")
        self.driver.get("https://gemini.google.com/gem/f0d42ce03437")
        time.sleep(3)
        console.print("[bold yellow]Initialisation rapide...[/bold yellow]")
        time.sleep(3)

    def restart_session(self):
        console.print("[bold cyan]🔄 Redémarrage de la session Gem...[/bold cyan]")
        try:
            if self.driver:
                self.driver.quit()
        except Exception:
            pass
        self.setup_driver()
        self.open_gemini()
        console.print("[bold green]✅ Session Gem redémarrée.[/bold green]")

    def send_question(self, enhanced_prompt, new_chat=True):
        console.print(f"\n[cyan]Sending (Start Tag: {self.current_start_tag} | End Tag: {self.current_end_tag} | Nouvelle session Gem: {new_chat}):[/cyan] {enhanced_prompt[:120]}...")

        try:
            time.sleep(0.5)

            if new_chat:
                try:
                    self.driver.get("https://gemini.google.com/gem/f0d42ce03437")
                    time.sleep(2.5)
                except Exception as e:
                    console.print(f"[yellow]⚠️ Impossible d'ouvrir la page de la Gem : {e}[/yellow]")
            else:
                console.print("[dim]↪️ Envoi du résultat du skill dans la conversation active en cours...[/dim]")
                time.sleep(0.8)

            js_send_script = """
                let inputElement = null;
                let richTextarea = document.querySelector('rich-textarea');

                if (richTextarea) {
                    inputElement = richTextarea.querySelector('p[contenteditable="true"]') ||
                                  richTextarea.querySelector('div[contenteditable="true"]') ||
                                  richTextarea.querySelector('[contenteditable="true"]');
                }

                if (!inputElement) {
                    inputElement = document.querySelector('[contenteditable="true"]');
                }

                if (inputElement) {
                    inputElement.click();
                    inputElement.focus();
                    
                    document.execCommand('selectAll', false, null);
                    document.execCommand('insertText', false, arguments[0]);

                    if (inputElement.textContent.trim() === "") {
                        inputElement.textContent = arguments[0];
                    }

                    inputElement.dispatchEvent(new Event('input', { bubbles: true }));
                    inputElement.dispatchEvent(new Event('change', { bubbles: true }));

                    setTimeout(() => {
                        let sendButton = document.querySelector('button[aria-label*="Send"]') ||
                                        document.querySelector('button[type="submit"]') ||
                                        Array.from(document.querySelectorAll('button')).find(btn =>
                                            btn.getAttribute('aria-label')?.includes('Send') ||
                                            btn.textContent.includes('Send') ||
                                            btn.querySelector('mat-icon')?.textContent.includes('send')
                                        );

                        if (sendButton && !sendButton.disabled) {
                            sendButton.click();
                        } else {
                            let event = new KeyboardEvent('keydown', {
                                key: 'Enter',
                                code: 'Enter',
                                keyCode: 13,
                                which: 13,
                                bubbles: true
                            });
                            inputElement.dispatchEvent(event);
                        }
                    }, 100);

                    return true;
                }
                return false;
            """

            result = self.driver.execute_script(js_send_script, enhanced_prompt)
            return result
        except Exception as e:
            console.print(f"[red]Erreur dans send_question : {e}[/red]")
            return False

    def wait_and_get_response(self, timeout=120):
        start_time = time.time()
        last_log_time = 0
        last_console_check = 0

        while time.time() - start_time < timeout:
            elapsed_time = time.time() - start_time

            try:
                full_page_text = self.driver.find_element(By.TAG_NAME, "body").text

                if time.time() - last_log_time > 2:
                    try:
                        with open("page_logs.txt", "w", encoding="utf-8") as log_file:
                            log_file.write(f"--- Logs de la page à {time.strftime('%H:%M:%S')} (écoulé: {int(elapsed_time)}s) ---\n\n")
                            log_file.write(full_page_text)
                    except Exception:
                        pass
                    last_log_time = time.time()

                # Les balises apparaissent 1 fois dans NOTRE message (consigne du prompt)
                # et 1 fois dans la réponse de l'IA : la réponse n'est donc complète
                # que lorsque chaque balise est présente au moins 2 fois.
                # (Le message envoyé peut être replié par Gemini : on ne compte donc pas les occurrences.)
                # Dans notre prompt, la balise de FIN est citée AVANT la balise de début : si le
                # prompt est visible, aucune balise de fin ne suit sa balise de début.
                last_start = full_page_text.rfind(self.current_start_tag)
                has_start = last_start != -1
                end_idx = -1
                if has_start:
                    start_idx = last_start + len(self.current_start_tag)
                    end_idx = full_page_text.find(self.current_end_tag, start_idx)
                has_end = end_idx != -1

                if time.time() - last_console_check >= 1.0:
                    console.print(f"[dim][{int(elapsed_time)}s] Lecture page -> Balise début: {'TROUVÉE ✅' if has_start else 'Recherche... ⏳'} | Balise fin: {'TROUVÉE ✅' if has_end else 'Recherche... ⏳'}[/dim]")
                    last_console_check = time.time()

                # Extraction stricte : uniquement ce qui est entre la dernière balise de début
                # et la balise de fin qui la suit. Le reste de la page est ignoré.
                if has_start:
                    if end_idx != -1:
                        response = full_page_text[start_idx:end_idx].strip()
                        if len(response) > 0:
                            console.print(f"[green]✅ Réponse détectée entre les balises en {int(elapsed_time * 10) / 10}s ![/green]")
                            return response

            except Exception:
                pass

            time.sleep(0.1)

        return "⚠️ Erreur : Temps d'attente dépassé (2 minutes sans réponse valide)."

    def ask(self, question, gui):
        current_prompt = question
        is_first_message = True
        tour = 0

        while tour < 10:
            tour += 1

            enhanced_prompt, self.current_start_tag, self.current_end_tag, _, _ = generate_enhanced_prompt(current_prompt)

            try:
                with open("tag_actif.txt", "w", encoding="utf-8") as f:
                    f.write(f"Balise début : {self.current_start_tag}\n")
                    f.write(f"Balise fin : {self.current_end_tag}\n")
            except Exception:
                pass

            if not self.send_question(enhanced_prompt, new_chat=is_first_message):
                console.print("[bold red]❌ [GREG] Échec de l'envoi du message à Gemini.[/bold red]")
                return "❌ Échec de l'envoi"

            is_first_message = False

            gui.update_display(f"Réflexion (tour {tour})...", state="thinking")
            response = self.wait_and_get_response(timeout=120)

            if "Temps d'attente dépassé" in response:
                console.print(f"[bold red]❌ [GREG] Timeout en attendant la réponse de Gemini au tour {tour}.[/bold red]")
                return response

            console.print(f"[dim]📨 [GREG] Réponse brute reçue (tour {tour}, {len(response)} caractères).[/dim]")

            # On vérifie si un skill est présent dans la réponse
            has_skill, skill_name, result = skills_module.registry.parse_and_execute(response)

            if has_skill:
                console.print(
                    f"[bold magenta]🛠️ [GREG] Skill(s) '{skill_name}' détecté(s) et exécuté(s) (Tour {tour}). "
                    f"Envoi du résultat à l'IA pour le tour suivant...[/bold magenta]"
                )
                gui.update_display(f"Exécution : {skill_name}...", state="thinking")

                current_prompt = f"Résultat de l'exécution du skill '{skill_name}' :\n{result}\n\nPoursuis ta tâche ou donne la réponse finale."
                time.sleep(0.5)
            else:
                console.print(f"[dim]💬 [GREG] Aucun skill détecté au tour {tour} : fin de la conversation et lecture vocale.[/dim]")
                return response

        return "⚠️ Erreur : Nombre maximum de tours de skills atteint (boucle de sécurité)."

    def close(self):
        if self.driver:
            try:
                self.driver.quit()
            except Exception:
                pass

def main():
    console.print("[bold cyan]🚀 Initialisation de Hawk Assistant (Clawd on Desk + Piper TTS local)[/bold cyan]")

    voice_engine = PiperVoiceEngine(model_path="C:/Piper/fr_FR-tom-medium.onnx")
    gemini = GeminiChat()
    assistant = VoiceAssistant(voice_engine=voice_engine)

    def cleanup():
        console.print("[yellow]Fermeture du navigateur Chrome virtuel...[/yellow]")
        gemini.close()
        for filename in ["tag_actif.txt", "page_logs.txt"]:
            if os.path.exists(filename):
                try:
                    os.remove(filename)
                except Exception:
                    pass
        console.print("[green]Nettoyage terminé.[/green]")

    gui = ClawdBridge(on_close_callback=cleanup)

    def full_restart():
        console.print("[bold cyan]🔄 Redémarrage complet de Greg...[/bold cyan]")
        try:
            cleanup()
        except Exception:
            pass
        try:
            gui.shutdown()
        except Exception:
            pass
        python = sys.executable
        os.execv(python, [python] + sys.argv)

    def restart_gemini_session():
        try:
            gemini.restart_session()
            return "Session Gem rechargée avec succès !"
        except Exception as e:
            console.print(f"[bold red]Erreur rechargement session : {e}[/bold red]")
            return f"Erreur : {e}"

    def update_skills():
        try:
            importlib.reload(skills_module)
            console.print(f"[bold green]✅ Skills rechargés avec succès ! Skills disponibles : {list(skills_module.registry.get_all_skills().keys())}[/bold green]")
            return "Skills mis à jour avec succès !"
        except Exception as e:
            console.print(f"[bold red]Erreur rechargement skills : {e}[/bold red]")
            return f"Erreur : {e}"

    def full_close():
        try:
            cleanup()
        except Exception:
            pass
        try:
            gui.shutdown()
        except Exception:
            pass
        os._exit(0)

    is_busy = threading.Event()

    def handle_voice_submit(text):
        if not is_busy.is_set():
            assistant.add_manual_command(text)

    panel = ControlPanel(
        on_restart=full_restart,
        on_restart_session=restart_gemini_session,
        on_update_skills=update_skills,
        on_close=full_close,
        on_voice_submit=handle_voice_submit
    )

    try:
        gemini.open_gemini()
        gui.update_display("Prêt ! Dites 'OK Greg'.", state="listening")

        while not gui.closed.is_set():
            question = assistant.listen_continuously(gui)
            if not question:
                continue

            if question.lower() in ['quitter', 'exit', 'stop', 'quitte']:
                voice_engine.speak("Arrêt de l'assistant.")
                break

            is_busy.set()
            response = gemini.ask(question, gui)
            is_busy.clear()

            if "Temps d'attente dépassé" in response or "Échec de l'envoi" in response:
                gui.update_display(response, state="timeout")
            else:
                speech_text = skills_module.clean_text_for_speech(response)

                gui.update_display(response, state="response")
                if speech_text:
                    voice_engine.speak(speech_text)
                else:
                    console.print("[dim]🔇 [GREG] Réponse finale silencieuse (mode [SILENCE] ou texte vide après nettoyage).[/dim]")
                gui.update_display("", state="done")
    except KeyboardInterrupt:
        pass
    except Exception as e:
        console.print(f"[red]Erreur critique: {e}[/red]")
    finally:
        gui.shutdown()

if __name__ == "__main__":
    main()