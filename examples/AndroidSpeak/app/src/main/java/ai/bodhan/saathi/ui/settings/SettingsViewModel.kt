package ai.bodhan.saathi.ui.settings

import android.app.Application
import ai.bodhan.saathi.SaathiApplication
import ai.bodhan.saathi.data.ApiModel
import ai.bodhan.saathi.data.AppSettings
import androidx.lifecycle.AndroidViewModel
import androidx.lifecycle.viewModelScope
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.SharingStarted
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.stateIn
import kotlinx.coroutines.launch

class SettingsViewModel(application: Application) : AndroidViewModel(application) {

    private val settingsRepository = (application as SaathiApplication).settingsRepository

    val settings: StateFlow<AppSettings> = settingsRepository.settingsFlow
        .stateIn(viewModelScope, SharingStarted.WhileSubscribed(5000), AppSettings())

    private val _apiKeyInputs = MutableStateFlow(
        ApiModel.entries.associateWith { settingsRepository.getApiKey(it).orEmpty() },
    )
    val apiKeyInputs: StateFlow<Map<ApiModel, String>> = _apiKeyInputs

    private val _saved = MutableStateFlow(false)
    val saved: StateFlow<Boolean> = _saved

    fun onApiKeyInputChange(model: ApiModel, value: String) {
        _apiKeyInputs.value = _apiKeyInputs.value + (model to value)
        _saved.value = false
    }

    fun saveApiKeys() {
        _apiKeyInputs.value.forEach { (model, key) -> settingsRepository.setApiKey(model, key.trim()) }
        _saved.value = true
    }

    fun setMyLanguage(code: String) {
        viewModelScope.launch { settingsRepository.setMyLanguage(code) }
    }

    fun setLocalLanguage(code: String) {
        viewModelScope.launch { settingsRepository.setLocalLanguage(code) }
    }

    fun setVoice(voice: String) {
        viewModelScope.launch { settingsRepository.setVoice(voice) }
    }

    fun setAutoPlay(enabled: Boolean) {
        viewModelScope.launch { settingsRepository.setAutoPlay(enabled) }
    }
}
