"""Banner display for the Neuro Sama module."""

from . import console


def _colorize_logo(text: str) -> str:
    """Applies complex coloring rules to the ASCII logo."""
    lines = text.strip("\n").split("\n")
    colored_lines = []

    # --- Color and Range Definitions ---
    NEURO_RANGE = (0, 43)
    S_RANGE = (48, 55)
    A1_RANGE = (56, 63)
    M_RANGE = (64, 74)
    A2_RANGE = (75, 82)

    NEURO_START_RGB = console._hex_to_rgb(console.PALETTE["NEURO_PINK_START"])
    NEURO_END_RGB = console._hex_to_rgb(console.PALETTE["NEURO_PINK_END"])

    SAMA_COLORS_RGB = {
        "S": console._hex_to_rgb(console.PALETTE["SAMA_PINK"]),
        "A1": console._hex_to_rgb(console.PALETTE["SAMA_PURPLE"]),
        "M": console._hex_to_rgb(console.PALETTE["SAMA_TEAL"]),
        "A2": console._hex_to_rgb(console.PALETTE["SAMA_ORANGE"]),
    }

    for line in lines:
        new_line = ""
        for i, char in enumerate(line):
            if char.isspace():
                new_line += char
                continue

            color_code = ""
            # NEURO Gradient
            if NEURO_RANGE[0] <= i <= NEURO_RANGE[1]:
                fraction = (i - NEURO_RANGE[0]) / (NEURO_RANGE[1] - NEURO_RANGE[0])
                r = int(NEURO_START_RGB[0] + (NEURO_END_RGB[0] - NEURO_START_RGB[0]) * fraction)
                g = int(NEURO_START_RGB[1] + (NEURO_END_RGB[1] - NEURO_START_RGB[1]) * fraction)
                b = int(NEURO_START_RGB[2] + (NEURO_END_RGB[2] - NEURO_START_RGB[2]) * fraction)
                color_code = console.rgb_fg(r, g, b)
            # SAMA Solid Colors
            elif S_RANGE[0] <= i <= S_RANGE[1]:
                r, g, b = SAMA_COLORS_RGB["S"]
                color_code = console.rgb_fg(r, g, b)
            elif A1_RANGE[0] <= i <= A1_RANGE[1]:
                r, g, b = SAMA_COLORS_RGB["A1"]
                color_code = console.rgb_fg(r, g, b)
            elif M_RANGE[0] <= i <= M_RANGE[1]:
                r, g, b = SAMA_COLORS_RGB["M"]
                color_code = console.rgb_fg(r, g, b)
            elif A2_RANGE[0] <= i <= A2_RANGE[1]:
                r, g, b = SAMA_COLORS_RGB["A2"]
                color_code = console.rgb_fg(r, g, b)

            new_line += f"{color_code}{char}" if color_code else char

        colored_lines.append(new_line)

    return "\n".join(colored_lines) + console.RESET


def display_banner():
    """Displays an ASCII art banner for the Neuro Sama module."""
    logo_text = r"""

███╗   ██╗███████╗██╗   ██╗██████╗  ██████╗     ███████╗ █████╗ ███╗   ███╗ █████╗
████╗  ██║██╔════╝██║   ██║██╔══██╗██╔═══██╗    ██╔════╝██╔══██╗████╗ ████║██╔══██╗
██╔██╗ ██║█████╗  ██║   ██║██████╔╝██║   ██║    ███████╗███████║██╔████╔██║███████║
██║╚██╗██║██╔══╝  ██║   ██║██╔══██╗██║   ██║    ╚════██║██╔══██║██║╚██╔╝██║██╔══██║
██║ ╚████║███████╗╚██████╔╝██║  ██║╚██████╔╝    ███████║██║  ██║██║ ╚═╝ ██║██║  ██║
╚═╝  ╚═══╝╚══════╝ ╚═════╝ ╚═╝  ╚═╝ ╚═════╝     ╚══════╝╚═╝  ╚═╝╚═╝     ╚═╝╚═╝  ╚═╝

"""

    colored_logo = _colorize_logo(logo_text)
    print(colored_logo)

    # Display welcome message
    messages = [
        "Hello everyone, Neuro-sama here."
    ]
    console.box_it_up(messages, title="Neuro wakes up", border_color=console.THEME["STATUS"])