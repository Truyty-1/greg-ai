import os
import re
import sys
import json
import shutil
import queue
import threading
import subprocess
import urllib.request

import pygame
from rich.console import Console

console = Console()

# ---------------------------------------------------------------------------
# Réglages de la voix corrigés (Ralentie et alourdie/plus grave)
# ---------------------------------------------------------------------------
LENGTH_SCALE = 1.4      # Plus haut = plus lent (augmenté pour ralentir le débit)
NOISE_SCALE = 0.668     # Ajusté pour stabiliser le timbre
NOISE_W = 0.333           
PAUSE_MS = 250          # Pause légèrement rallongée entre les phrases
MAX_CHUNK_CHARS = 220 

DEFAULT_MODEL = "C:/Piper/fr_FR-tom-medium.onnx"
HF_BASE = "https://huggingface.co/rhasspy/piper-voices/resolve/main/fr/fr_FR/tom/medium/"


class PiperVoiceEngine:
    """Synthèse vocale locale via Piper TTS (voix masculine française).

    - Le modèle est chargé UNE seule fois en mémoire (pas de relance de piper.exe à chaque phrase).
    - La phrase suivante est synthétisée pendant que la précédente est jouée (pas de blanc).
    - Aucun fichier temporaire : l'audio est joué directement depuis la mémoire.
    - Repli automatique sur piper.exe si le paquet Python `piper-tts` n'est pas installé.
    """

    def __init__(self, model_path=DEFAULT_MODEL):
        self.model_path = model_path
        self.stop_requested = False
        self.voice = None         # modèle Piper chargé (API Python)
        self.syn_config = None
        self.piper_bin = None      # repli : exécutable piper.exe
        self.sample_rate = 22050
        self.ready = False

        self._ensure_model_files()
        if not os.path.exists(self.model_path):
            console.print(f"[bold red]❌ Modèle Piper introuvable : '{self.model_path}'[/bold red]")
            return

        self.sample_rate = self._read_sample_rate()
        self._load_backend()

        if not self.ready:
            console.print(
                "[bold red]❌ Aucun moteur Piper disponible. Installe-le avec :  "
                "pip install piper-tts[/bold red]"
            )
            return

        # Le mixer est initialisé légèrement en dessous de la fréquence nominale 
        # pour donner un rendu plus grave et chaleureux à la voix.
        try:
            pygame.mixer.quit()
            pygame.mixer.init(frequency=int(self.sample_rate * 0.92), size=-16, channels=1, buffer=1024)
        except Exception as e:
            console.print(f"[bold red]❌ Impossible d'initialiser l'audio : {e}[/bold red]")
            self.ready = False
            return

        # Échauffement : évite le délai de la toute première réponse
        try:
            self._synthesize("Bonjour.")
        except Exception:
            pass

        mode = "Python (rapide)" if self.voice else "piper.exe (repli)"
        console.print(f"[bold green]✅ Moteur vocal Piper prêt ! Mode : {mode} | Modèle : {self.model_path}[/bold green]")

    # ------------------------------------------------------------------
    # Préparation
    # ------------------------------------------------------------------
    def _ensure_model_files(self):
        """Télécharge la voix Tom (gratuite) si elle est absente."""
        json_path = self.model_path + ".json"
        if os.path.exists(self.model_path) and os.path.exists(json_path):
            return
        name = os.path.basename(self.model_path)
        if name != "fr_FR-tom-medium.onnx":
            return  # on ne télécharge automatiquement que la voix par défaut
        try:
            os.makedirs(os.path.dirname(self.model_path) or ".", exist_ok=True)
            for suffix in ("", ".json"):
                target = self.model_path + suffix
                if not os.path.exists(target):
                    console.print(f"[yellow]⬇️ Téléchargement de {name}{suffix}...[/yellow]")
                    urllib.request.urlretrieve(HF_BASE + name + suffix, target)
        except Exception as e:
            console.print(f"[bold red]❌ Téléchargement impossible : {e}[/bold red]")

    def _read_sample_rate(self):
        try:
            with open(self.model_path + ".json", "r", encoding="utf-8") as f:
                return int(json.load(f)["audio"]["sample_rate"])
        except Exception:
            return 22050

    def _load_backend(self):
        # 1) Paquet Python piper-tts : le modèle reste en mémoire
        try:
            from piper import PiperVoice
            self.voice = PiperVoice.load(self.model_path)
            try:
                from piper import SynthesisConfig
                self.syn_config = SynthesisConfig(
                    length_scale=LENGTH_SCALE,
                    noise_scale=NOISE_SCALE,
                    noise_w_scale=NOISE_W,
                )
            except Exception:
                self.syn_config = None  # ancienne version : paramètres passés à l'appel
            self.ready = True
            return
        except Exception as e:
            self.voice = None
            console.print(f"[yellow]⚠️ Paquet Python piper-tts indisponible ({e}). Repli sur piper.exe...[/yellow]")

        # 2) Repli : exécutable piper.exe
        self.piper_bin = self._find_piper_binary()
        self.ready = self.piper_bin is not None

    def _find_piper_binary(self):
        found = shutil.which("piper")
        if found:
            return found
        for path in ("C:/Piper/piper.exe", "C:/Piper/piper/piper.exe",
                     os.path.expanduser("~/piper/piper.exe")):
            if os.path.exists(path):
                return path
        return None

    # ------------------------------------------------------------------
    # Nettoyage et découpage du texte
    # ------------------------------------------------------------------
    @staticmethod
    def clean_text(text):
        t = text
        t = re.sub(r"\[(?:début|fin)_[a-fA-F0-9]+\]", " ", t)             # balises du prompt
        t = re.sub(r"```.*?```", " ", t, flags=re.DOTALL)                 # blocs de code
        t = re.sub(r"https?://\S+", " ", t)                               # liens
        t = re.sub(r"[\U0001F000-\U0001FAFF\u2600-\u27BF\uFE0F]", " ", t) # emojis
        t = t.replace("°C", " degrés").replace("%", " pour cent").replace("&", " et ")
        t = t.replace("…", "...")
        t = re.sub(r"^\s*[-*•]\s+", "", t, flags=re.MULTILINE)            # puces
        t = re.sub(r"^\s*#+\s*", "", t, flags=re.MULTILINE)               # titres
        t = re.sub(r"[*_`#|\[\]{}<>~\\/^=]", " ", t)                    # symboles markdown
        t = re.sub(r"-{2,}", " ", t)
        t = re.sub(r"\s*\n+\s*", ". ", t)                                # retours ligne = pause
        t = re.sub(r"\.(\s*\.)+", ".", t)
        t = re.sub(r"\s{2,}", " ", t)
        return t.strip()

    @staticmethod
    def split_sentences(text):
        parts = re.split(r"(?<=[.!?;:])\s+", text)
        chunks = []
        for p in parts:
            p = p.strip()
            if not p:
                continue
            # coupe les phrases trop longues aux virgules
            while len(p) > MAX_CHUNK_CHARS:
                cut = p.rfind(",", 0, MAX_CHUNK_CHARS)
                if cut < 40:
                    cut = p.rfind(" ", 0, MAX_CHUNK_CHARS)
                if cut < 40:
                    cut = MAX_CHUNK_CHARS
                chunks.append(p[:cut + 1].strip())
                p = p[cut + 1:].strip()
            if p:
                chunks.append(p)
        # fusionne les morceaux minuscules avec le suivant (plus naturel)
        merged = []
        for c in chunks:
            if merged and len(merged[-1]) < 25 and len(merged[-1]) + len(c) < MAX_CHUNK_CHARS:
                merged[-1] = merged[-1] + " " + c
            else:
                merged.append(c)
        # ignore ce qui ne contient aucune lettre/chiffre
        return [c for c in merged if re.search(r"\w", c)]

    # ------------------------------------------------------------------
    # Synthèse
    # ------------------------------------------------------------------
    def _synthesize(self, text):
        """Retourne de l'audio PCM 16 bits mono (bytes)."""
        if self.voice is not None:
            if hasattr(self.voice, "synthesize_stream_raw"):  # piper-tts 1.2.x
                pcm = b"".join(self.voice.synthesize_stream_raw(
                    text, length_scale=LENGTH_SCALE, noise_scale=NOISE_SCALE, noise_w=NOISE_W))
            else:                                                   # piper-tts 1.3+
                if self.syn_config is not None:
                    chunks = self.voice.synthesize(text, syn_config=self.syn_config)
                else:
                    chunks = self.voice.synthesize(text)
                pcm = b"".join(c.audio_int16_bytes for c in chunks)
        else:
            cmd = [self.piper_bin, "--model", self.model_path, "--output_raw",
                   "--length_scale", str(LENGTH_SCALE),
                   "--noise_scale", str(NOISE_SCALE),
                   "--noise_w", str(NOISE_W)]
            flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
            result = subprocess.run(cmd, input=text.encode("utf-8"),
                                    stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                    creationflags=flags, timeout=60)
            if result.returncode != 0:
                raise RuntimeError(result.stderr.decode("utf-8", errors="ignore").strip())
            pcm = result.stdout

        if not pcm:
            raise RuntimeError("audio vide")
        # petit silence en fin de phrase
        pcm += b"\x00\x00" * int(self.sample_rate * PAUSE_MS / 1000)
        return pcm

    # ------------------------------------------------------------------
    # Lecture
    # ------------------------------------------------------------------
    def stop(self):
        """Coupe immédiatement la lecture en cours."""
        self.stop_requested = True
        try:
            pygame.mixer.stop()
        except Exception:
            pass

    def speak(self, text):
        """Synthétise et joue le texte, la phrase suivante se prépare pendant la lecture."""
        if not self.ready:
            console.print("[bold red]❌ Synthèse impossible : moteur Piper non prêt.[/bold red]")
            return

        console.print(f"[bold magenta]🤖 Assistant (vocal Piper):[/bold magenta] {text}")

        sentences = self.split_sentences(self.clean_text(text))
        if not sentences:
            return

        audio_queue = queue.Queue(maxsize=2)  # max 2 phrases d'avance
        DONE = object()

        def producer():
            for s in sentences:
                if self.stop_requested:
                    break
                try:
                    audio_queue.put(self._synthesize(s))
                except Exception as e:
                    console.print(f"[bold red]❌ Erreur synthèse ('{s[:40]}...') : {e}[/bold red]")
            audio_queue.put(DONE)

        threading.Thread(target=producer, daemon=True).start()

        while True:
            pcm = audio_queue.get()
            if pcm is DONE:
                break
            if self.stop_requested:
                console.print("[yellow]🛑 Lecture vocale interrompue.[/yellow]")
                # vide la file pour débloquer le producteur
                while audio_queue.get() is not DONE:
                    pass
                break
            try:
                channel = pygame.mixer.Sound(buffer=pcm).play()
                while channel is not None and channel.get_busy():
                    if self.stop_requested:
                        channel.stop()
                        break
                    pygame.time.wait(20)
            except Exception as e:
                console.print(f"[bold red]Erreur lecture audio : {e}[/bold red]")