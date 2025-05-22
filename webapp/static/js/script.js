document.addEventListener('DOMContentLoaded', () => {
    const recordButton = document.getElementById('recordButton');
    const statusDiv = document.getElementById('status');
    const userTextSpan = document.getElementById('userText');
    const assistantTextSpan = document.getElementById('assistantText');
    const audioPlayer = document.getElementById('audioPlayer');

    let mediaRecorder;
    let audioChunks = [];
    let isRecording = false;

    async function startRecording() {
        if (isRecording) return;
        try {
            const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
            mediaRecorder = new MediaRecorder(stream); // Potrebbe essere necessario specificare il mimeType

            mediaRecorder.ondataavailable = event => {
                audioChunks.push(event.data);
            };

            mediaRecorder.onstop = async () => {
                const audioBlob = new Blob(audioChunks, { type: 'audio/wav' }); // Tentiamo WAV, altrimenti il backend deve convertire
                audioChunks = [];

                statusDiv.textContent = 'Elaborazione audio...';
                recordButton.disabled = true;
                recordButton.classList.remove('recording');
                recordButton.textContent = 'Elaborazione...';


                const formData = new FormData();
                formData.append('audio_file', audioBlob, 'user_recording.wav');

                try {
                    const response = await fetch('/process-audio/', {
                        method: 'POST',
                        body: formData
                    });

                    recordButton.disabled = false;
                    recordButton.textContent = 'Tieni Premuto e Parla';

                    if (response.ok) {
                        const result = await response.json(); // Aspettiamo JSON ora

                        userTextSpan.textContent = result.user_text;
                        assistantTextSpan.textContent = result.llm_response_text;
                        
                        // L'audio è ora un URL (o un data URI) nel JSON
                        if (result.audio_response_data_url) {
                            audioPlayer.src = result.audio_response_data_url;
                            audioPlayer.play();
                            statusDiv.textContent = 'Risposta ricevuta. In riproduzione...';
                        } else {
                            statusDiv.textContent = 'Risposta testuale ricevuta, ma nessun audio.';
                        }
                        
                        audioPlayer.onended = () => {
                            statusDiv.textContent = 'Pronto per una nuova richiesta.';
                        };

                    } else {
                        const errorResult = await response.json().catch(() => ({ detail: 'Errore server non JSON' }));
                        statusDiv.textContent = `Errore: ${errorResult.detail || response.statusText}`;
                        console.error('Server error:', errorResult);
                        userTextSpan.textContent = '';
                        assistantTextSpan.textContent = '';
                    }
                } catch (error) {
                    statusDiv.textContent = 'Errore di rete o elaborazione.';
                    console.error('Fetch error:', error);
                    recordButton.disabled = false;
                    recordButton.textContent = 'Tieni Premuto e Parla';
                    userTextSpan.textContent = '';
                    assistantTextSpan.textContent = '';
                }
                stream.getTracks().forEach(track => track.stop());
            };

            mediaRecorder.start();
            isRecording = true;
            statusDiv.textContent = 'Registrazione in corso... Rilascia per inviare.';
            recordButton.classList.add('recording');
            recordButton.textContent = 'Rilascia per Inviare';
            userTextSpan.textContent = '';
            assistantTextSpan.textContent = '';
        } catch (err) {
            console.error('Errore accesso al microfono:', err);
            statusDiv.textContent = 'Errore accesso al microfono. Controlla i permessi.';
            isRecording = false; // Assicurati che lo stato sia corretto
            recordButton.classList.remove('recording');
            recordButton.textContent = 'Tieni Premuto e Parla';
        }
    }

    function stopRecording() {
        if (!isRecording || !mediaRecorder || mediaRecorder.state === 'inactive') {
            // Se non stiamo registrando attivamente ma il pulsante è premuto (es. errore microfono)
            isRecording = false;
            recordButton.classList.remove('recording');
            recordButton.textContent = 'Tieni Premuto e Parla';
            statusDiv.textContent = 'Pronto. Errore precedente o nessuna registrazione avviata.';
            return;
        }
        mediaRecorder.stop();
        isRecording = false;
        // Lo stato del pulsante e del testo viene gestito in mediaRecorder.onstop
    }

    recordButton.addEventListener('mousedown', startRecording);
    recordButton.addEventListener('mouseup', stopRecording);
    recordButton.addEventListener('mouseleave', () => { // Se il mouse esce mentre si preme
        if (isRecording) {
            stopRecording();
        }
    });
    // Per dispositivi touch
    recordButton.addEventListener('touchstart', (e) => { e.preventDefault(); startRecording(); }, { passive: false });
    recordButton.addEventListener('touchend', (e) => { e.preventDefault(); stopRecording(); });

    statusDiv.textContent = 'Pronto. Tieni premuto il pulsante per parlare.';
});