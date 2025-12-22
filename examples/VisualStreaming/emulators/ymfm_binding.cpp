// ymfm Python binding with per-channel output
// Uses chanmask to get individual channel outputs

#include <pybind11/pybind11.h>
#include <pybind11/numpy.h>
#include <cstdint>
#include <vector>
#include <cstring>
#include <cmath>

// ymfm includes
#include "ymfm_opn.h"
#include "ymfm_opn.cpp"
#include "ymfm_adpcm.cpp"
#include "ymfm_ssg.cpp"

namespace py = pybind11;

class YmfmInterface : public ymfm::ymfm_interface {
public:
    virtual void ymfm_sync_mode_write(uint8_t data) override {}
    virtual void ymfm_sync_check_interrupts() override {}
    virtual void ymfm_set_timer(uint32_t tnum, int32_t duration) override {}
    virtual void ymfm_set_busy_end(uint32_t clocks) override {}
    virtual bool ymfm_is_busy() override { return false; }
    virtual uint8_t ymfm_external_read(ymfm::access_class type, uint32_t address) override { return 0; }
    virtual void ymfm_external_write(ymfm::access_class type, uint32_t address, uint8_t data) override {}
};

// Extended ym2612 that exposes per-channel output
class ym2612_perchannel : public ymfm::ym2612 {
public:
    ym2612_perchannel(ymfm::ymfm_interface &intf) : ymfm::ym2612(intf) {}

    // Clock the chip once
    void clock_once() {
        m_fm.clock(0x3F);  // All 6 channels
    }

    // Get output for a single channel (call after clock_once)
    void get_channel_output(int channel, output_data &output) {
        output.clear();
        // rshift=0 for full amplitude, clipmax=32767
        m_fm.output(output, 0, 32767, 1 << channel);

        // Handle DAC for channel 5
        // DAC data is 9-bit, already sign-converted via XOR 0x80 in register write
        // Use ymfm's formula to sign-extend: int16_t(m_dac_data << 7) >> 7
        // This gives range -256 to +255
        if (channel == 5 && m_dac_enable) {
            int16_t dac_signed = int16_t(m_dac_data << 7) >> 7;
            // Scale to match FM output range (-8192 to 8191): multiply by 32
            int32_t dacval = static_cast<int32_t>(dac_signed) * 32;
            output.data[0] = dacval;
            output.data[1] = dacval;
        }
    }

    // Access DAC state
    uint16_t get_dac_data() const { return m_dac_data; }
    bool get_dac_enable() const { return m_dac_enable != 0; }
};

class YM2612Wrapper {
public:
    static constexpr int NUM_CHANNELS = 6;
    static constexpr int SAMPLE_RATE = 44100;
    static constexpr uint32_t CLOCK = 7670453;
    static constexpr double INTERNAL_RATE = CLOCK / 144.0;

    YM2612Wrapper() : m_chip(m_interface) {
        m_chip.reset();
        m_resample_accum = 0.0;
        m_resample_ratio = INTERNAL_RATE / SAMPLE_RATE;
        for (int i = 0; i < NUM_CHANNELS; i++) {
            m_prev_output[i] = 0.0f;
            m_curr_output[i] = 0.0f;
        }
    }

    void reset() {
        m_chip.reset();
        m_resample_accum = 0.0;
        for (int i = 0; i < NUM_CHANNELS; i++) {
            m_prev_output[i] = 0.0f;
            m_curr_output[i] = 0.0f;
        }
    }

    void write(int port, int addr, int data) {
        uint32_t offset = (port == 0) ? 0 : 2;
        m_chip.write(offset, static_cast<uint8_t>(addr));
        m_chip.write(offset + 1, static_cast<uint8_t>(data));
    }

    py::tuple generate_samples(int num_samples) {
        std::vector<py::array_t<float>> outputs;
        for (int ch = 0; ch < NUM_CHANNELS; ch++) {
            outputs.push_back(py::array_t<float>(num_samples));
        }

        std::vector<float*> out_ptrs;
        for (int ch = 0; ch < NUM_CHANNELS; ch++) {
            out_ptrs.push_back(outputs[ch].mutable_data());
        }

        for (int i = 0; i < num_samples; i++) {
            m_resample_accum += m_resample_ratio;

            while (m_resample_accum >= 1.0) {
                m_resample_accum -= 1.0;

                for (int ch = 0; ch < NUM_CHANNELS; ch++) {
                    m_prev_output[ch] = m_curr_output[ch];
                }

                // Clock the chip once
                m_chip.clock_once();

                // Get output for each channel separately
                for (int ch = 0; ch < NUM_CHANNELS; ch++) {
                    ymfm::ym2612::output_data output;
                    m_chip.get_channel_output(ch, output);

                    // Average stereo to mono, normalize
                    // ymfm outputs 14-bit signed (-8192 to 8191), not 16-bit
                    float val = (static_cast<float>(output.data[0]) +
                                 static_cast<float>(output.data[1])) / 2.0f / 8192.0f;
                    m_curr_output[ch] = val;
                }
            }

            float frac = static_cast<float>(m_resample_accum);
            for (int ch = 0; ch < NUM_CHANNELS; ch++) {
                float val = m_prev_output[ch] * (1.0f - frac) + m_curr_output[ch] * frac;
                out_ptrs[ch][i] = std::max(-1.0f, std::min(1.0f, val));
            }
        }

        py::tuple result(NUM_CHANNELS);
        for (int ch = 0; ch < NUM_CHANNELS; ch++) {
            result[ch] = outputs[ch];
        }
        return result;
    }

    bool is_active(int channel) {
        if (channel == 5 && m_chip.get_dac_enable()) return true;
        return std::abs(m_curr_output[channel]) > 0.001f;
    }

    bool is_dac_enabled() {
        return m_chip.get_dac_enable();
    }

private:
    YmfmInterface m_interface;
    ym2612_perchannel m_chip;
    double m_resample_accum;
    double m_resample_ratio;
    float m_prev_output[NUM_CHANNELS];
    float m_curr_output[NUM_CHANNELS];
};

PYBIND11_MODULE(_ymfm, m) {
    m.doc() = "ymfm YM2612 Python bindings with per-channel output";

    py::class_<YM2612Wrapper>(m, "YM2612")
        .def(py::init<>())
        .def("reset", &YM2612Wrapper::reset)
        .def("write", &YM2612Wrapper::write)
        .def("generate_samples", &YM2612Wrapper::generate_samples)
        .def("is_active", &YM2612Wrapper::is_active)
        .def("is_dac_enabled", &YM2612Wrapper::is_dac_enabled);
}
