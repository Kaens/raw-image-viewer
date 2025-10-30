// C++, SDL2 + ImGui viewer for raw image bitstreams
// Made by Kae <TG@kaens, GitHub@Kaens>

#include <SDL.h>
#include <SDL_video.h>
#include <cstdio>
#include <cstdlib>
#include <cstdint>
#include <cstring>
#include <vector>
#include <string>
#include <fstream>
#include <iostream>
#include <optional>
#include <algorithm>
#include <cassert>
#include <filesystem>

#include "imgui.h"
#include "imgui_impl_sdl2.h"
#include "imgui_impl_opengl3.h"
#include <GL/gl.h>
#include "nfd_sdl2.h"
#include "stb_image_write.h"

using namespace std;

// ------------------------------ Simple bitreader utilities ------------------------------
static inline uint32_t read_bits_msb(const uint8_t* data, size_t total_bits, size_t bitpos, int nbits) {
    // read nbits MSB-first from data starting at bitpos; not optimised
    uint32_t val = 0;
    for (int i = 0; i < nbits; ++i) {
        size_t p = bitpos + i;
        uint8_t bit = 0;
        if (p < total_bits) bit = (data[p >> 3] >> (7 - (p & 7))) & 1u;
        val = (val << 1) | bit;
    }
    return val;
}

static inline uint32_t read_bits_lsb(
  const uint8_t* data,
  const size_t total_bits,
  const size_t bitpos,
  const int nbits
) {
    size_t val = 0;
    for (auto i = 0; i < nbits; ++i) {
        size_t p = bitpos + i;
        uint8_t bit = 0;
        if (p < total_bits) {
            size_t bidx = p >> 3;
            uint8_t bit_in_byte = p & 7;
            bit = (data[bidx] >> bit_in_byte) & 1u;
        }
        val |= static_cast<size_t>(bit << i);
    }
    return val;
}

static inline uint64_t adjust_endianness_pixel(const size_t pixel_val, const int bpp, const bool little_endian) {
    if (!little_endian || bpp <= 8) return pixel_val & ((bpp >= 64) ? ~0ull : ((1ull << bpp) - 1ull));
    const uint8_t nbytes = (bpp + 7) / 8;
    uint8_t bytes[8] = {};
    for (auto i = 0; i < nbytes; ++i) {
        const auto shift = (nbytes - 1 - i) * 8;
        bytes[i] = (pixel_val >> shift) & 0xFFu;
    }
    // reverse the bytes for little-endian interpretation
    uint64_t out = 0;
    for (auto i = 0; i < nbytes; ++i) {
        out = (out << 8) | bytes[nbytes - 1 - i];
    }
    return out & ((1ull << bpp) - 1ull);
}

// ------------------------------ Preset description ------------------------------
struct Field { char name; int bits; }; // 'r','g','b','a','y' (y=gray)
struct Preset {
    string label;
    vector<int> bpps;
    vector<Field> fields;
    bool lsb_order {false};
};

static vector<Preset> build_presets() { //not all of these are common
    vector<Preset> p;
    p.push_back({"1-bit: Monochrome (MSB)", {1}, {{'y',1}}});
    p.push_back({"4-bit: Grayscale", {4}, {{'y',4}}});
    p.push_back({"4-bit: 2R-1G-1B", {4}, {{'r',2}, {'g',1}, {'b',1}}});
    p.push_back({"8-bit: Grayscale", {8}, {{'y',8}}});
    p.push_back({"8-bit: R3-G3-B2", {8}, {{'r',3}, {'g',3}, {'b',2}}});
    p.push_back({"8-bit: B3-G3-R2", {8}, {{'b',3}, {'g',3}, {'r',2}}});
    p.push_back({"8-bit: R2-G3-B3", {8}, {{'r',2}, {'g',3}, {'b',3}}});
    p.push_back({"8-bit: A2-R2-G2-B2", {8}, {{'a',2}, {'r',2}, {'g',2}, {'b',2}}});
    p.push_back({"8-bit: A1-R2-G3-B2", {8}, {{'a',1}, {'r',2}, {'g',3}, {'b',2}}});
    p.push_back({"16-bit: R5-G6-B5", {16}, {{'r',5}, {'g',6}, {'b',5}}});
    p.push_back({"16-bit: A1-R5-G5-B5", {16}, {{'a',1}, {'r',5}, {'g',5}, {'b',5}}});
    p.push_back({"16-bit: R4-G4-B4-A4", {16}, {{'r',4}, {'g',4}, {'b',4}, {'a',4}}});
    p.push_back({"16-bit: R3-G4-B3", {16}, {{'r',3}, {'g',4}, {'b',3}}});
    p.push_back({"16-bit: B3-G4-R3", {16}, {{'b',3}, {'g',4}, {'r',3}}});
    p.push_back({"16-bit: A1-R3-G3-B3", {16}, {{'a',1}, {'r',3}, {'g',3}, {'b',3}}});
    p.push_back({"24-bit: R-G-B", {24}, {{'r',8}, {'g',8}, {'b',8}}});
    p.push_back({"24-bit: B-G-R", {24}, {{'b',8}, {'g',8}, {'r',8}}});
    p.push_back({"32-bit: R-G-B-A", {32}, {{'r',8}, {'g',8}, {'b',8}, {'a',8}}});
    p.push_back({"32-bit: A-R-G-B", {32}, {{'a',8}, {'r',8}, {'g',8}, {'b',8}}});
    p.push_back({"32-bit: A-B-G-R", {32}, {{'a',8}, {'b',8}, {'g',8}, {'r',8}}});
    p.push_back({"32-bit: B-G-R-A", {32}, {{'b',8}, {'g',8}, {'r',8}, {'a',8}}});
    return p;
}

// ------------------------------ Renderer ------------------------------
struct ViewerState {
    vector<uint8_t> data;
    string filename;
    int stofs{};
    int width_px{256}; // "int" as per InputInt in ImGui
    int bpp{8};
    int bit_align{};
    int preset_idx{3}; // 8-bit grayscale, corresponds with bpp
    bool bit_order_msb{true};
    bool byte_order_le{false};
};

static inline uint8_t scale_to_8(const uint64_t raw, const uint8_t bits) {
    if (!bits) return 0;
    if (bits >= 8) {
        if (bits == 8) return static_cast<uint8_t>(raw & 0xFF);
        // more bits: scale down
        return static_cast<uint8_t>((raw >> (bits - 8)) & 0xFF);
    }
    // expand to 0..255
    const uint64_t maxv = (1ull << bits) - 1;
    return static_cast<uint8_t>((raw * 255u + (maxv / 2)) / maxv);
}

// Render a viewport (width x rows) into an RGBA buffer (row-major)
static void render_viewport(const ViewerState& s, const Preset& preset, const int rows,
                            vector<uint8_t>& out_pixels, uint32_t& out_rows_rendered) {
    const size_t total_bits = s.data.size() * 8;
    const size_t start_bit = s.stofs * 8 + s.bit_align;
    if (start_bit >= total_bits) {
        out_rows_rendered = 0;
        out_pixels.clear();
        return;
    }
    const auto width = max<int>(1, s.width_px);
    const auto pixels_to_render = rows * width;
    const auto pixels_available = (total_bits - start_bit) / s.bpp;
    if (pixels_available == 0) {
        out_rows_rendered = 0;
        out_pixels.clear();
        return;
    }
    const auto actual_pixels = min<uint32_t>(pixels_to_render, pixels_available);
    const auto rows_needed = (actual_pixels + width - 1) / width;
    out_rows_rendered = rows_needed;
    out_pixels.assign(rows_needed * width * 4, 0);

    const uint8_t* raw = s.data.data();
    size_t bitpos = start_bit;

    for (uint32_t p = 0; p < rows_needed * width; ++p) {
        const uint32_t x = p % width;
        const auto y = p / width;
        uint8_t* dst = &out_pixels[(y * width + x) * 4];
        if (p >= pixels_available) {
            // transparent
            dst[0] = dst[1] = dst[2] = dst[3] = 0;
            continue;
        }
        uint64_t pixel_val = 0;
        if (s.bit_order_msb) {
            pixel_val = read_bits_msb(raw, total_bits, bitpos, s.bpp);
        } else {
            pixel_val = read_bits_lsb(raw, total_bits, bitpos, s.bpp);
        }
        bitpos += s.bpp;
        pixel_val = adjust_endianness_pixel(pixel_val, s.bpp, s.byte_order_le);

        // fields are MSB->LSB in preset.fields
        int cur_shift = s.bpp;
        uint8_t r = 255, g = 255, b = 255, a = 255;
        for (const auto &[name, bits] : preset.fields) {
            const int use = min(bits, cur_shift);
            uint64_t rawcomp = 0;
            if (cur_shift > 0 && use>0) {
                rawcomp = (pixel_val >> (cur_shift - use)) & ((1ull<<use)-1ull);
            }
            cur_shift -= use;
            const uint8_t val8 = scale_to_8(rawcomp, use);
            switch (name) {
                case 'r': r = val8; break;
                case 'g': g = val8; break;
                case 'b': b = val8; break;
                case 'a': a = val8; break;
                case 'y': r = g = b = val8; break;
                default: r = g = b = 0;
            }
        }
        dst[0] = r; dst[1] = g; dst[2] = b; dst[3] = a;
    }
}

// Save RGBA buffer to PNG (stb)
static bool save_png(const string &filename, const int w, const int h, const vector<uint8_t>& buf) {
    if (static_cast<int>(buf.size()) < w*h*4) return false;
    const int stride = w * 4;
    const int res = stbi_write_png(filename.c_str(), w, h, 4, buf.data(), stride);
    return res != 0;
}

// Helper: load file into ViewerState
static bool load_file_into(ViewerState &S, const string &path) {
    if (path.empty()) return false;
    ifstream in(path, ios::binary | ios::ate);
    if (!in) return false;
    const auto sz = in.tellg();
    in.seekg(0, ios::beg);
    vector<uint8_t> tmp((size_t)sz);
    in.read(reinterpret_cast<char *>(tmp.data()), sz);
    S.data.swap(tmp);
    S.filename = path;
    S.stofs = 0;
    S.bit_align = 0;
    return true;
}

// ------------------------------ Main program ------------------------------
int main(int argc, char** argv) {
    // Init SDL + GL + ImGui
    if (SDL_Init(SDL_INIT_VIDEO|SDL_INIT_TIMER|SDL_INIT_EVENTS) != 0) {
        fprintf(stderr, "Error: SDL_Init failed: %s\n", SDL_GetError());
        return 1;
    }

    // GL attributes (core profile)
    SDL_GL_SetAttribute(SDL_GL_CONTEXT_FLAGS, 0);
    SDL_GL_SetAttribute(SDL_GL_CONTEXT_PROFILE_MASK, SDL_GL_CONTEXT_PROFILE_CORE);
    SDL_GL_SetAttribute(SDL_GL_CONTEXT_MAJOR_VERSION, 3);
    SDL_GL_SetAttribute(SDL_GL_CONTEXT_MINOR_VERSION, 0);

    auto window_flags = static_cast<SDL_WindowFlags>(SDL_WINDOW_OPENGL | SDL_WINDOW_RESIZABLE | SDL_WINDOW_ALLOW_HIGHDPI);
    SDL_Window* window = SDL_CreateWindow("Raw Viewer (SDL2 + ImGui)", SDL_WINDOWPOS_CENTERED, SDL_WINDOWPOS_CENTERED, 1200, 800, window_flags);
    if (!window) {
        fprintf(stderr, "Error: SDL_CreateWindow failed: %s\n", SDL_GetError());
        return 1;
    }

    SDL_GLContext gl_context = SDL_GL_CreateContext(window);
    if (!gl_context) {
        fprintf(stderr, "Error: SDL_GL_CreateContext failed: %s\n", SDL_GetError());
        return 1;
    }
    SDL_GL_MakeCurrent(window, gl_context);
    SDL_GL_SetSwapInterval(1);

    // Setup Dear ImGui context
    IMGUI_CHECKVERSION();
    ImGui::CreateContext();
    ImGuiIO& io = ImGui::GetIO(); (void)io;
    io.ConfigFlags |= ImGuiConfigFlags_DockingEnable; // enable docking
    //io.ConfigFlags |= ImGuiConfigFlags_ViewportsEnable; // enable multi-vp / Windows
    //io.ConfigFlags |= ImGuiConfigFlags_NavEnableKeyboard; // enable keyboard controls
    io.ConfigDpiScaleFonts = true;
    io.ConfigDpiScaleViewports = true;
    io.IniFilename = "imgui_layout.ini"; // persist layout
    ImGui::StyleColorsDark();
    //io.Fonts->AddFontFromFileTTF("mO'sOul_v1.0.ttf", 14);

    // Setup Platform/Renderer backends
    ImGui_ImplSDL2_InitForOpenGL(window, gl_context);
    ImGui_ImplOpenGL3_Init("#version 130");

    // Prepare presets
    auto presets = build_presets();
    ViewerState S;
    S.data.clear();

    //bool show_demo = false;

    // Texture for display
    GLuint tex = 0;
    int tex_w = 0, tex_h = 0;

    // UI state
    string path;
    //int selected_preset = 0;

    bool want_quit = false;
    bool save_requested = false;
    bool load_requested = false;
    vector<uint8_t> rgba_buf;

    if (argc > 1) {
        //put the filename into path:
        path = argv[1];
        load_requested = true;
    }


    // main loop
    while (!want_quit) {
        // Poll events
        SDL_Event event;
        while (SDL_PollEvent(&event)) {
            ImGui_ImplSDL2_ProcessEvent(&event);
            if (event.type == SDL_QUIT) want_quit = true;
            if (event.type == SDL_WINDOWEVENT && event.window.event == SDL_WINDOWEVENT_CLOSE && event.window.windowID == SDL_GetWindowID(window)) {
                want_quit = true;
            }

            // SDL2 drop file
            if (event.type == SDL_DROPFILE) {
                if (char* dropped_filedir = event.drop.file) {
                    path = dropped_filedir;
                    load_requested = true; // defer actual load to main loop
                    SDL_free(dropped_filedir);
                }
            }

            // keyboard navigation (when ImGui not capturing keyboard)
            if (event.type == SDL_KEYDOWN && !io.WantCaptureKeyboard) {
                SDL_Keycode k = event.key.keysym.sym;
                // Shift+Arrows for 1-by-1 offset
                if (event.key.keysym.mod & KMOD_SHIFT) {
                    if (k == SDLK_UP) {
                        S.stofs = (S.stofs > S.width_px) ? S.stofs - S.width_px : 0;
                    } else if (k == SDLK_DOWN) {
                        S.stofs = (static_cast<size_t>(S.stofs + S.width_px * 16) >= S.data.size() - 16)
                        ? S.stofs
                        : S.stofs + S.width_px;
                    } else if (k == SDLK_LEFT) {
                        S.stofs = (S.stofs > 0) ? S.stofs - 1 : 0;
                    } else if (k == SDLK_RIGHT) {
                        S.stofs = (static_cast<size_t>(S.stofs + S.width_px * 16) >= S.data.size() - 16)
                        ? S.stofs
                        : S.stofs + 1;
                    }
                }
                // Alt+arrows for bpp/bit-align
                else if (event.key.keysym.mod & KMOD_ALT) {
                    if (k == SDLK_UP) {
                        // cycle bpp up
                        constexpr int choices[]{1,4,8,16,24,32};
                        int i{}; while (i < 4 && choices[i] != S.bpp) ++i;
                        i = (i + 1) % 4; S.bpp = choices[i];
                    } else if (k == SDLK_DOWN) {
                        // cycle bpp down
                        constexpr int choices[]{1,4,8,16,24,32};
                        int i{}; while (i < 4 && choices[i] != S.bpp) ++i;
                        i = (i + 3) % 4; S.bpp = choices[i];
                    } else if (k == SDLK_LEFT) {
                        S.bit_align = max<uint8_t>(0, S.bit_align - 1);
                    } else if (k == SDLK_RIGHT) {
                        S.bit_align = min<uint8_t>(7, S.bit_align + 1);
                    }
                }
                else if (k == SDLK_LEFT)
                    S.width_px = max<int>(1, S.width_px - 1);
                else if (k == SDLK_RIGHT)
                    S.width_px = S.width_px + 1;
                else if (k == SDLK_UP)
                    S.stofs = (S.stofs > S.width_px * 16) ? S.stofs - S.width_px * 16 : 0;
                else if (k == SDLK_DOWN)
                    S.stofs = (static_cast<size_t>(S.stofs + S.width_px * 16) >= S.data.size() - 16)
                        ? S.stofs
                        : S.stofs + S.width_px * 16;
                else if (k == SDLK_PAGEUP) {
                    // compute visible rows
                    int win_w, win_h;
                    SDL_GetWindowSize(window, &win_w, &win_h);
                    int image_h = max(1, win_h);
                    int visible_rows = image_h;
                    int visible_pixels = S.width_px * visible_rows;
                    int visible_bits = visible_pixels * S.bpp;
                    int page_bits = (visible_bits * 2) / 3;
                    auto start_bit = S.stofs * 8 + S.bit_align;
                    auto nstart = start_bit - page_bits;
                    if (nstart < 0) nstart = 0;
                    S.stofs = nstart / 8;
                    S.bit_align = nstart % 8;
                }
                else if (k == SDLK_PAGEDOWN) {
                    int win_w, win_h;
                    SDL_GetWindowSize(window, &win_w, &win_h);
                    int visible_rows = max(1, win_h);
                    int visible_pixels = S.width_px * visible_rows;
                    int visible_bits = visible_pixels * S.bpp;
                    int page_bits = (visible_bits * 2) / 3;
                    auto start_bit = S.stofs * 8 + S.bit_align;
                    int64_t nstart = start_bit + page_bits;
                    if (int64_t total_bits = static_cast<int64_t>(S.data.size()) * 8;
                        nstart > total_bits - S.bpp
                    )
                        nstart = max<int64_t>(0, total_bits - S.bpp);
                    S.stofs = nstart / 8;
                    S.bit_align = static_cast<uint8_t>(nstart % 8);
                }
            }
        }

        // Start the Dear ImGui frame
        ImGui_ImplSDL2_NewFrame();
        ImGui_ImplOpenGL3_NewFrame();
        ImGui::NewFrame();

        // Dockspace (create once per frame; windows will dock into it)
        ImGui::DockSpaceOverViewport(0, ImGui::GetMainViewport());

        // Left-side UI (Controls) - give an initial size and allow docking
        ImGui::SetNextWindowSize(ImVec2(320, 400), ImGuiCond_FirstUseEver);
        ImGui::Begin("Controls", nullptr, ImGuiWindowFlags_None);
        ImGuiIO& uiio = ImGui::GetIO();
        float ui_scale = uiio.FontGlobalScale > 0.0f ? uiio.FontGlobalScale : 1.0f;

        ImGui::PushItemWidth(120.0f * ui_scale);
        ImGui::InputText("File", path.data(), path.size());
        ImGui::SameLine();
        if (ImGui::Button("...")) {
            nfdchar_t *outPath = nullptr;
            if (nfdresult_t result = NFD_OpenDialog(&outPath, nullptr, 0, nullptr); result == NFD_OKAY) {
                path = outPath;
                NFD_FreePath(outPath);
                load_requested = true;
            } else if (result == NFD_CANCEL) {
                // user cancelled; do nothing
            } else {
                cerr << "NFD error: " << NFD_GetError() << endl;
            }
        }
        ImGui::PopItemWidth();

        if (ImGui::Button("Load file")) {
            load_requested = true;
        }
        ImGui::SameLine();
        if (ImGui::Button("Save visible PNG")) {
            save_requested = true;
        }

        ImGui::Separator();

        ImGui::PushItemWidth(130.0f * ui_scale);
        ImGui::InputInt("Width (px/row)", &S.width_px);
        if (S.width_px < 1) S.width_px = 1;
        ImGui::InputInt("Start offset", &S.stofs);
        ImGui::InputInt("Bit alignment", &S.bit_align);
        if (S.bit_align < 0) S.bit_align = 0;
        if (S.bit_align > 7) S.bit_align = 7;
        ImGui::InputInt("Bits per pixel", &S.bpp);
        // Constrain bpp to {1,4,8,16} via buttons
        if (ImGui::Button("1 BPP")) S.bpp = 1;
        ImGui::SameLine(); if (ImGui::Button("4 BPP")) S.bpp = 4;
        ImGui::SameLine(); if (ImGui::Button("8 BPP")) S.bpp = 8;
        ImGui::SameLine(); if (ImGui::Button("16 BPP")) S.bpp = 16;
        ImGui::PopItemWidth();

        ImGui::Separator();

        // Preset selector
        ImGui::Text("Presets:");
        for (int i = 0; i < static_cast<int>(presets.size()); ++i)
            if (ImGui::Selectable(presets[i].label.c_str(), i == S.preset_idx)) {
                S.preset_idx = i;
                // set bits-per-pixel to the preset total so 24/32 presets actually work
                int total_bits = 0;
                for (const auto &f : presets[i].fields) total_bits += f.bits;
                if (total_bits > 0) S.bpp = total_bits;
            }

        ImGui::Separator();
        ImGui::Text("Orders:");
        ImGui::Checkbox("Bit-order MSB", &S.bit_order_msb);
        ImGui::Checkbox("Byte-order LE", &S.byte_order_le);

        if (ImGui::Button("Center start (0)")) {
            S.stofs = 0;
            S.bit_align = 0;
        }

        ImGui::Separator();

        ImGui::Text("Hotkeys:");
        ImGui::Text("Up/Dn Offset -+ 16 lines");
        ImGui::Text("Lt/Rt Width -+");
        ImGui::Text("Shift+Up/Dn Ofs -+ 1 line");
        ImGui::Text("Shift+Lt/Rt Ofs -+ 1 byte");
        ImGui::Text("Alt+Up/Dn Change BPP");
        ImGui::Text("Alt+Lt/Rt Change bit-align");

        ImGui::End();

        // Right-side: image area - occupy remaining space; place texture inside a child to control layout
        ImGui::Begin("Image", nullptr, ImGuiWindowFlags_NoScrollbar | ImGuiWindowFlags_NoScrollWithMouse);
        ImGui::BeginChild("ImageArea", ImVec2(0,0), false, ImGuiWindowFlags_NoMove);

        ImVec2 avail = ImGui::GetContentRegionAvail();
        int display_w = static_cast<int>(avail.x);
        int display_h = static_cast<int>(avail.y);
        if (display_w < 1) display_w = 64;
        if (display_h < 1) display_h = 64;

        // perform deferred load if requested
        if (load_requested) {
            if (!load_file_into(S, path.c_str())) {
                cerr << "Failed to open file: " << path << endl;
            }
            load_requested = false;
        }

        // Render viewport into RGBA buffer of size width x visible_rows (visible rows = display_h)
        int rows = display_h;
        vector<uint8_t> pixels;
        uint32_t rows_rendered = 0;
        render_viewport(S, presets[S.preset_idx], rows, pixels, rows_rendered);

        // upload to GL texture
        if (rows_rendered > 0) {
            if (tex == 0) glGenTextures(1, &tex);
            if (tex) {
                tex_w = S.width_px;
                tex_h = static_cast<int>(rows_rendered);
                glBindTexture(GL_TEXTURE_2D, tex);
                glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MIN_FILTER, GL_NEAREST);
                glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MAG_FILTER, GL_NEAREST);
                glPixelStorei(GL_UNPACK_ALIGNMENT, 1);
                glTexImage2D(GL_TEXTURE_2D, 0, GL_RGBA, tex_w, tex_h, 0, GL_RGBA, GL_UNSIGNED_BYTE, pixels.data());
            }
        }

        // draw the texture in ImGui, centered
        if (tex != 0) {
            float cur_x = ImGui::GetCursorPosX();
            float avail_x = ImGui::GetContentRegionAvail().x;
            auto img_w = static_cast<float>(S.width_px);
            auto img_h = static_cast<float>(rows_rendered);
            ImGui::SetCursorPosX(cur_x + (avail_x - img_w) * 0.5f);
            ImGui::Image(tex, ImVec2(img_w, img_h));
        } else {
            ImGui::Text("No pixels to render");
        }

        ImGui::EndChild();
        ImGui::End();

        // Save PNG if requested (saves the whole current rendered rectangle into PNG)
        if (save_requested && rows_rendered > 0) {
            int outc{-1};
            while (save_requested && outc++ < 999) {
                std::string outname = format("rawviewer{:03}.png", outc);
                if (filesystem::exists(outname)) continue;
                cerr << "saving \"" << outname << "\"...";
                if (save_png(outname, tex_w, tex_h, pixels)) {
                    cerr << "Saved " << outname << endl;
                    save_requested = false;
                }
            }
            if (save_requested) {
                cerr << "Failed to save PNG\n";
                save_requested = false;
            }
        }

        // Render ImGui
        ImGui::Render();
        int fb_w = static_cast<int>(io.DisplaySize.x);
        int fb_h = static_cast<int>(io.DisplaySize.y);
        glViewport(0,0, fb_w, fb_h);
        glClearColor(0.1f,0.1f,0.12f,1.0f);
        glClear(GL_COLOR_BUFFER_BIT);
        ImGui_ImplOpenGL3_RenderDrawData(ImGui::GetDrawData());
        SDL_GL_SwapWindow(window);
    }

    // Cleanup
    if (tex) glDeleteTextures(1, &tex);
    ImGui_ImplOpenGL3_Shutdown();
    ImGui_ImplSDL2_Shutdown();
    ImGui::DestroyContext();

    SDL_GL_DeleteContext(gl_context);
    SDL_DestroyWindow(window);
    SDL_Quit();

    return 0;
}
