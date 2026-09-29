package ai.bodhan.saathi.ui.conversation

import android.app.Application
import ai.bodhan.saathi.R
import ai.bodhan.saathi.SaathiApplication
import ai.bodhan.saathi.audio.AudioPlayer
import ai.bodhan.saathi.audio.WavRecorder
import ai.bodhan.saathi.data.ApiModel
import ai.bodhan.saathi.data.AppSettings
import ai.bodhan.saathi.network.BodhanException
import androidx.lifecycle.AndroidViewModel
import androidx.lifecycle.viewModelScope
import java.io.File
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.SharingStarted
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.stateIn
import kotlinx.coroutines.launch

class ConversationViewModel(application: Application) : AndroidViewModel(application) {

    private val app = application as SaathiApplication
    private val settingsRepository = app.settingsRepository
    private val bodhanRepository = app.bodhanRepository
    private val recorder = WavRecorder()
    private val audioPlayer = AudioPlayer()

    val settings: StateFlow<AppSettings> = settingsRepository.settingsFlow
        .stateIn(viewModelScope, SharingStarted.WhileSubscribed(5000), AppSettings())

    private val _entries = MutableStateFlow<List<ConversationEntry>>(emptyList())
    val entries: StateFlow<List<ConversationEntry>> = _entries

    private val _status = MutableStateFlow<ProcessingStatus?>(null)
    val status: StateFlow<ProcessingStatus?> = _status

    private val _recordingSpeaker = MutableStateFlow<Speaker?>(null)
    val recordingSpeaker: StateFlow<Speaker?> = _recordingSpeaker

    private val _error = MutableStateFlow<String?>(null)
    val error: StateFlow<String?> = _error

    fun hasApiKeys(): Boolean = settingsRepository.hasAllApiKeys()

    fun clearError() {
        _error.value = null
    }

    fun onMicTap(speaker: Speaker) {
        val currentlyRecording = _recordingSpeaker.value
        if (currentlyRecording == speaker) {
            val cacheDir = getApplication<Application>().cacheDir
            val wavFile = File(cacheDir, "input_${System.currentTimeMillis()}.wav")
            val recorded = recorder.stop(wavFile)
            _recordingSpeaker.value = null
            if (recorded == null) {
                _error.value = getApplication<Application>().getString(R.string.no_audio_captured)
            } else {
                processRecordedAudio(speaker, recorded)
            }
            return
        }

        if (currentlyRecording != null) return // the other mic is already recording

        if (!hasApiKeys()) {
            _error.value = getApplication<Application>().getString(R.string.api_key_missing)
            return
        }

        recorder.start(getApplication<Application>().cacheDir)
        _recordingSpeaker.value = speaker
    }

    fun cancelRecording() {
        if (_recordingSpeaker.value != null) {
            recorder.cancel()
            _recordingSpeaker.value = null
        }
    }

    fun sendTypedText(speaker: Speaker, text: String) {
        if (text.isBlank()) return
        if (!hasApiKeys()) {
            _error.value = getApplication<Application>().getString(R.string.api_key_missing)
            return
        }
        viewModelScope.launch {
            translateAndSpeak(speaker, text, settings.value)
        }
    }

    fun replay(entry: ConversationEntry) {
        entry.audioFile?.let { audioPlayer.play(it) }
    }

    fun setMyLanguage(code: String) {
        viewModelScope.launch { settingsRepository.setMyLanguage(code) }
    }

    fun setLocalLanguage(code: String) {
        viewModelScope.launch { settingsRepository.setLocalLanguage(code) }
    }

    fun swapLanguages() {
        viewModelScope.launch {
            val current = settings.value
            settingsRepository.setMyLanguage(current.localLanguage)
            settingsRepository.setLocalLanguage(current.myLanguage)
        }
    }

    private fun processRecordedAudio(speaker: Speaker, wavFile: File) {
        viewModelScope.launch {
            val transcribeKey = settingsRepository.getApiKey(ApiModel.TRANSCRIBE)
            if (transcribeKey.isNullOrBlank()) {
                _error.value = getApplication<Application>().getString(R.string.api_key_missing)
                wavFile.delete()
                return@launch
            }
            val current = settings.value
            val sourceLang = if (speaker == Speaker.ME) current.myLanguage else current.localLanguage
            try {
                _status.value = ProcessingStatus(speaker, Stage.TRANSCRIBING)
                val originalText = bodhanRepository.transcribe(transcribeKey, wavFile, sourceLang)
                if (originalText.isBlank()) {
                    _error.value = getApplication<Application>().getString(R.string.no_audio_captured)
                    return@launch
                }
                translateAndSpeak(speaker, originalText, current)
            } catch (e: BodhanException) {
                _error.value = e.message
            } catch (e: Exception) {
                _error.value = e.message ?: getApplication<Application>().getString(R.string.generic_error)
            } finally {
                _status.value = null
                wavFile.delete()
            }
        }
    }

    /** Translates [originalText] and speaks the result. Always leaves [_status] cleared on exit. */
    private suspend fun translateAndSpeak(speaker: Speaker, originalText: String, current: AppSettings) {
        val translateKey = settingsRepository.getApiKey(ApiModel.TRANSLATE)
        val speakKey = settingsRepository.getApiKey(ApiModel.SPEAK)
        if (translateKey.isNullOrBlank() || speakKey.isNullOrBlank()) {
            _error.value = getApplication<Application>().getString(R.string.api_key_missing)
            return
        }
        val sourceLang = if (speaker == Speaker.ME) current.myLanguage else current.localLanguage
        val targetLang = if (speaker == Speaker.ME) current.localLanguage else current.myLanguage
        try {
            _status.value = ProcessingStatus(speaker, Stage.TRANSLATING)
            val translatedText = bodhanRepository.translate(translateKey, originalText, sourceLang, targetLang)

            _status.value = ProcessingStatus(speaker, Stage.SPEAKING)
            val outFile = File(getApplication<Application>().cacheDir, "speech_${System.currentTimeMillis()}.wav")
            val audioFile = bodhanRepository.speak(speakKey, translatedText, targetLang, current.voice, outFile)

            val entry = ConversationEntry(
                id = System.currentTimeMillis(),
                speaker = speaker,
                originalText = originalText,
                originalLangCode = sourceLang,
                translatedText = translatedText,
                translatedLangCode = targetLang,
                audioFile = audioFile,
            )
            _entries.value = _entries.value + entry
            if (current.autoPlay) {
                audioPlayer.play(audioFile)
            }
        } catch (e: BodhanException) {
            _error.value = e.message
        } catch (e: Exception) {
            _error.value = e.message ?: getApplication<Application>().getString(R.string.generic_error)
        } finally {
            _status.value = null
        }
    }

    override fun onCleared() {
        super.onCleared()
        recorder.cancel()
        audioPlayer.stop()
    }
}
