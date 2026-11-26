"""List Windows audio devices so you can set MIC_DEVICE / CABLE_DEVICE.

    python -m tools.list_devices
"""
from app.audio.capture import pyaudio_module


def main() -> None:
    pyaudio = pyaudio_module()
    pa = pyaudio.PyAudio()
    try:
        wasapi = pa.get_host_api_info_by_type(pyaudio.paWASAPI)
        print(f"Default output: {pa.get_device_info_by_index(wasapi['defaultOutputDevice'])['name']}")
        print(f"Default input:  {pa.get_device_info_by_index(wasapi['defaultInputDevice'])['name']}\n")
        for i in range(pa.get_device_count()):
            d = pa.get_device_info_by_index(i)
            if d["hostApi"] != wasapi["index"]:
                continue  # the app only uses WASAPI devices
            kind = "loopback" if d.get("isLoopbackDevice") else ("input" if d["maxInputChannels"] else "output")
            cable = "  <- VB-Cable" if "cable" in d["name"].lower() else ""
            print(f"[{i:>3}] {kind:<8} {int(d['defaultSampleRate'])} Hz  {d['name']}{cable}")
    finally:
        pa.terminate()


if __name__ == "__main__":
    main()
