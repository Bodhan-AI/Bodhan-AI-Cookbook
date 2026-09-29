package ai.bodhan.saathi.audio

import android.annotation.SuppressLint
import android.media.AudioFormat
import android.media.AudioRecord
import android.media.MediaRecorder
import java.io.File
import java.io.FileOutputStream

/**
 * Records mono 16-bit PCM audio and writes it out as a standard WAV file,
 * the format the Bodhan speech APIs expect.
 */
class WavRecorder(private val sampleRate: Int = 16000) {

    private var audioRecord: AudioRecord? = null
    private var recordingThread: Thread? = null
    @Volatile private var isRecording = false
    private var rawFile: File? = null

    val recording: Boolean get() = isRecording

    @SuppressLint("MissingPermission")
    fun start(cacheDir: File) {
        if (isRecording) return
        val minBufferSize = AudioRecord.getMinBufferSize(
            sampleRate,
            AudioFormat.CHANNEL_IN_MONO,
            AudioFormat.ENCODING_PCM_16BIT,
        )
        val bufferSize = if (minBufferSize > 0) minBufferSize * 2 else sampleRate * 2

        val record = AudioRecord(
            MediaRecorder.AudioSource.MIC,
            sampleRate,
            AudioFormat.CHANNEL_IN_MONO,
            AudioFormat.ENCODING_PCM_16BIT,
            bufferSize,
        )
        audioRecord = record
        val raw = File.createTempFile("saathi_rec_", ".pcm", cacheDir)
        rawFile = raw
        isRecording = true
        record.startRecording()

        recordingThread = Thread {
            val buffer = ByteArray(bufferSize)
            FileOutputStream(raw).use { out ->
                while (isRecording) {
                    val read = record.read(buffer, 0, buffer.size)
                    if (read > 0) out.write(buffer, 0, read)
                }
            }
        }.also { it.start() }
    }

    /** Stops recording and writes the captured audio into [outputWavFile]. Returns null if nothing was recorded. */
    fun stop(outputWavFile: File): File? {
        if (!isRecording) return null
        isRecording = false
        recordingThread?.join()
        recordingThread = null
        audioRecord?.apply {
            stop()
            release()
        }
        audioRecord = null

        val raw = rawFile ?: return null
        rawFile = null
        if (raw.length() == 0L) {
            raw.delete()
            return null
        }
        writeWavFile(raw, outputWavFile)
        raw.delete()
        return outputWavFile
    }

    /** Discards an in-progress recording without producing a file. */
    fun cancel() {
        isRecording = false
        recordingThread?.join()
        recordingThread = null
        audioRecord?.apply {
            stop()
            release()
        }
        audioRecord = null
        rawFile?.delete()
        rawFile = null
    }

    private fun writeWavFile(rawFile: File, wavFile: File) {
        val pcmData = rawFile.readBytes()
        val byteRate = sampleRate * 2 // 16-bit mono
        val header = ByteArray(44)
        writeHeader(header, pcmData.size, byteRate)
        FileOutputStream(wavFile).use { out ->
            out.write(header)
            out.write(pcmData)
        }
    }

    private fun writeHeader(header: ByteArray, dataLen: Int, byteRate: Int) {
        val totalDataLen = dataLen + 36
        header[0] = 'R'.code.toByte(); header[1] = 'I'.code.toByte(); header[2] = 'F'.code.toByte(); header[3] = 'F'.code.toByte()
        writeIntLE(header, 4, totalDataLen)
        header[8] = 'W'.code.toByte(); header[9] = 'A'.code.toByte(); header[10] = 'V'.code.toByte(); header[11] = 'E'.code.toByte()
        header[12] = 'f'.code.toByte(); header[13] = 'm'.code.toByte(); header[14] = 't'.code.toByte(); header[15] = ' '.code.toByte()
        writeIntLE(header, 16, 16) // PCM subchunk size
        header[20] = 1; header[21] = 0 // AudioFormat = PCM
        header[22] = 1; header[23] = 0 // NumChannels = mono
        writeIntLE(header, 24, sampleRate)
        writeIntLE(header, 28, byteRate)
        header[32] = 2; header[33] = 0 // BlockAlign = 16-bit mono
        header[34] = 16; header[35] = 0 // BitsPerSample
        header[36] = 'd'.code.toByte(); header[37] = 'a'.code.toByte(); header[38] = 't'.code.toByte(); header[39] = 'a'.code.toByte()
        writeIntLE(header, 40, dataLen)
    }

    private fun writeIntLE(header: ByteArray, offset: Int, value: Int) {
        header[offset] = (value and 0xff).toByte()
        header[offset + 1] = ((value shr 8) and 0xff).toByte()
        header[offset + 2] = ((value shr 16) and 0xff).toByte()
        header[offset + 3] = ((value shr 24) and 0xff).toByte()
    }
}
