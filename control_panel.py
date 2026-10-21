import tkinter as tk
from tkinter import ttk
import threading
import speech_recognition as sr


class ControlPanel:
    """Petite fenêtre de contrôle pour Greg avec onglets.

    Onglet 1 : Boutons d'administration (Redémarrer, Réinitialiser, Skills, Fermer)
    Onglet 2 : Micro interactif pour enregistrer et envoyer la parole à l'IA.
    """

    def __init__(self, on_restart=None, on_restart_session=None,
                 on_update_skills=None, on_close=None, on_voice_submit=None):
        self.on_restart = on_restart
        self.on_restart_session = on_restart_session
        self.on_update_skills = on_update_skills
        self.on_close = on_close
        self.on_voice_submit = on_voice_submit

        self.root = None
        self.status_var = None
        self.voice_status_var = None
        self._recording = threading.Event()
        self._processing = False

        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    # ------------------------------------------------------------------
    def _run(self):
        self.root = tk.Tk()
        self.root.title("Greg - Panneau de contrôle")
        self.root.geometry("340x320")
        self.root.resizable(False, False)
        self.root.attributes("-topmost", True)
        self.root.protocol("WM_DELETE_WINDOW", self._handle_close)

        style = ttk.Style()
        try:
            style.theme_use("clam")
        except Exception:
            pass

        tk.Label(self.root, text="🦅 Greg Assistant", font=("Segoe UI", 14, "bold")).pack(pady=(8, 2))

        # Création du système d'onglets
        notebook = ttk.Notebook(self.root)
        notebook.pack(fill="both", expand=True, padx=8, pady=5)

        tab_controls = ttk.Frame(notebook)
        tab_mic = ttk.Frame(notebook)

        notebook.add(tab_controls, text="🕹️ Contrôles")
        notebook.add(tab_mic, text="🎙️ Micro")

        # --- ONGLET 1 : CONTRÔLES ---
        self.status_var = tk.StringVar(value="Statut : En ligne ✅")
        tk.Label(tab_controls, textvariable=self.status_var, fg="#2e7d32", font=("Segoe UI", 9)).pack(pady=(5, 10))

        ttk.Button(tab_controls, text="🔄  Redémarrer Greg (complet)",
                   command=self._handle_restart).pack(fill="x", padx=15, pady=3)

        ttk.Button(tab_controls, text="💬  Recharger la session Gemini",
                   command=self._handle_restart_session).pack(fill="x", padx=15, pady=3)

        ttk.Button(tab_controls, text="🛠️  Mettre à jour les skills",
                   command=self._handle_update_skills).pack(fill="x", padx=15, pady=3)

        ttk.Button(tab_controls, text="❌  Fermer Greg complètement",
                   command=self._handle_close).pack(fill="x", padx=15, pady=3)

        # --- ONGLET 2 : MICRO ---
        tk.Label(tab_mic, text="Cliquez pour démarrer, recliquez pour arrêter", font=("Segoe UI", 9)).pack(pady=(15, 5))

        self.btn_mic = tk.Button(
            tab_mic, text="🎙️\nCliquez pour enregistrer", font=("Segoe UI", 11, "bold"),
            bg="#1976d2", fg="white", activebackground="#1565c0", activeforeground="white",
            relief="raised", bd=3, width=22, height=3, command=self._record_voice
        )
        self.btn_mic.pack(pady=10)

        self.voice_status_var = tk.StringVar(value="Prêt à l'écoute")
        tk.Label(tab_mic, textvariable=self.voice_status_var, fg="#555", font=("Segoe UI", 9, "italic")).pack(pady=5)

        self.root.mainloop()

    # ------------------------------------------------------------------
    def _record_voice(self):
        """Bascule : 1er clic = début de l'enregistrement, 2e clic = arrêt et envoi."""
        if self._processing:
            return  # transcription en cours, on ignore les clics
        if self._recording.is_set():
            # 2e clic : on demande l'arrêt, le thread d'enregistrement s'occupe du reste
            self._recording.clear()
            self.btn_mic.config(bg="#f57c00", text="⏳\nTraitement...")
            self.voice_status_var.set("Transcription...")
            return

        self._recording.set()
        self.btn_mic.config(bg="#d32f2f", text="🔴\nCliquez pour arrêter")
        self.voice_status_var.set("Enregistrement... cliquez pour arrêter")
        threading.Thread(target=self._record_worker, daemon=True).start()

    def _record_worker(self):
        recognizer = sr.Recognizer()
        try:
            frames = []
            with sr.Microphone() as source:
                chunk = 1024
                # Enregistre tant que l'utilisateur n'a pas recliqué
                while self._recording.is_set():
                    frames.append(source.stream.read(chunk))
                sample_rate = source.SAMPLE_RATE
                sample_width = source.SAMPLE_WIDTH

            self._processing = True
            if not frames:
                self.voice_status_var.set("Aucune voix capturée.")
                return

            audio = sr.AudioData(b"".join(frames), sample_rate, sample_width)
            text = recognizer.recognize_google(audio, language="fr-FR")

            if text and self.on_voice_submit:
                self.voice_status_var.set(f"Envoyé : {text}")
                self.on_voice_submit(text)
            else:
                self.voice_status_var.set("Aucune voix capturée.")
        except sr.UnknownValueError:
            self.voice_status_var.set("Je n'ai rien compris, réessayez.")
        except Exception as e:
            self.voice_status_var.set(f"Erreur : {e}")
        finally:
            self._recording.clear()
            self._processing = False
            self.btn_mic.config(bg="#1976d2", text="🎙️\nCliquez pour enregistrer")

    # ------------------------------------------------------------------
    def _handle_restart(self):
        self.set_status("Redémarrage complet...", color="#e65100")
        if self.on_restart:
            threading.Thread(target=self.on_restart, daemon=True).start()

    def _handle_restart_session(self):
        self.set_status("Rechargement de la session Gemini...", color="#1565c0")
        if self.on_restart_session:
            def task():
                result = self.on_restart_session()
                self.set_status(result or "Session Gemini rechargée !", color="#2e7d32")
            threading.Thread(target=task, daemon=True).start()

    def _handle_update_skills(self):
        self.set_status("Mise à jour des skills...", color="#1565c0")
        if self.on_update_skills:
            def task():
                result = self.on_update_skills()
                self.set_status(result or "Skills mis à jour !", color="#2e7d32")
            threading.Thread(target=task, daemon=True).start()

    def _handle_close(self):
        self.set_status("Fermeture en cours...", color="#c62828")
        if self.on_close:
            threading.Thread(target=self.on_close, daemon=True).start()
        try:
            self.root.after(400, self.root.destroy)
        except Exception:
            pass

    # ------------------------------------------------------------------
    def set_status(self, text, color="#2e7d32"):
        if not self.status_var or not self.root:
            return
        try:
            self.root.after(0, lambda: self.status_var.set(f"Statut : {text}"))
        except Exception:
            pass

    def destroy(self):
        try:
            if self.root:
                self.root.after(0, self.root.destroy)
        except Exception:
            pass