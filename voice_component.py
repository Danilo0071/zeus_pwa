import streamlit as st

def render_voice_component():
    # Componente HTML/JS leve para Web Speech API
    voice_html = """
    <div id="zeus-voice" style="position: fixed; bottom: 20px; right: 20px; z-index: 999;">
        <button id="mic-btn" style="padding: 15px; border-radius: 50%; background: #FF4B4B; color: white; border: none; font-size: 20px;">🎤</button>
        <span id="status" style="margin-left: 10px; font-size: 12px;">Aguardando...</span>
    </div>
    <script>
        const btn = document.getElementById('mic-btn');
        const status = document.getElementById('status');
        let recognizing = false;
        let recognition;

        if ('webkitSpeechRecognition' in window) {
            recognition = new webkitSpeechRecognition();
            recognition.continuous = true;
            recognition.interimResults = false;
            recognition.lang = 'pt-BR';

            recognition.onresult = (event) => {
                const last = event.results.length - 1;
                const text = event.results[last][0].transcript.toLowerCase();
                status.innerText = "Ouvi: " + text;
                
                if (text.includes('zeus')) {
                    // Comando após a palavra "Zeus"
                    const command = text.replace('zeus', '').trim();
                    if (command) {
                        // Envia para o backend via Streamlit (setComponentValue é síncrono)
                        window.parent.postMessage({isStreamlitMessage: true, type: "streamlit:setComponentValue", value: command}, "*");
                        // Fala de confirmação
                        const utterance = new SpeechSynthesisUtterance("Entendido. Processando " + command);
                        window.speechSynthesis.speak(utterance);
                    }
                }
            };
            
            btn.onclick = () => {
                if (recognizing) { recognition.stop(); recognizing = false; btn.style.background = "#FF4B4B"; }
                else { recognition.start(); recognizing = true; btn.style.background = "#4BFF4B"; status.innerText = "Ouvindo..."; }
            };
        } else {
            status.innerText = "Navegador sem suporte a voz.";
        }
    </script>
    """
    # Nota: O Streamlit Components nativo (st.components.v1.html) é isolado.
    # Para comunicação bidirecional real, use st.components.v1.html com JS que chame a API do Streamlit.
    # Abaixo usamos st.components.v1.html para renderizar e capturar via query params (fallback simples).
    st.components.v1.html(voice_html, height=100)