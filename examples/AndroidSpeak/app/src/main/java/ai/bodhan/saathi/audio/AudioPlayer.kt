package ai.bodhan.saathi.audio

import android.media.MediaPlayer
import java.io.File

class AudioPlayer {

    private var mediaPlayer: MediaPlayer? = null

    fun play(file: File, onCompletion: () -> Unit = {}) {
        stop()
        mediaPlayer = MediaPlayer().apply {
            setDataSource(file.absolutePath)
            setOnPreparedListener { it.start() }
            setOnCompletionListener {
                onCompletion()
                release()
            }
            setOnErrorListener { _, _, _ ->
                onCompletion()
                true
            }
            prepareAsync()
        }
    }

    fun stop() {
        mediaPlayer?.let {
            try {
                if (it.isPlaying) it.stop()
            } catch (_: IllegalStateException) {
                // already stopped/released
            }
            it.release()
        }
        mediaPlayer = null
    }
}
