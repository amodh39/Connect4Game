import sys
import socket
import pickle
import select
import re
import webbrowser
from collections import namedtuple

import pygame
import pygame.freetype
import pygame.mixer
import pyperclip # type: ignore

from pygame.sprite import RenderUpdates
from zeroconf import Zeroconf, ServiceBrowser, ServiceListener

from basic_version.connect4 import Connect4Game

from core.config import service_type, TIMEOUT_FOR_SERVICE_SEARCH
from core.player import Player
from core.level import Level
from pygame_version.utils import Board, Token, GlowingToken

from pygame_version.client import Client
from pygame_version.states import Choice, TokenState
from pygame_version.gamestate import GameState
from pygame_version.ui_tools import UIElement, CopyButtonElement, DisabledOrEnabledBtn, TokenButton, InputBox, FadeOutText, ErrorNotifier, StatusNotifier, ScoreBoard, CurrentRoundBoard, create_text_to_draw

from termcolor import colored  # type: ignore

clock = pygame.time.Clock()

BLUE = (106, 159, 181)
RED = (204, 0, 0)
WHITE = (255, 255, 255)
GRAY = (128, 128, 128)
LIGHT_GRAYISH_BLUE = (206, 229, 242)
TRANSPARENT = (0, 0, 0, 0)

GAME_CODE_LENGTH = 16
MAX_IP_ADDR_LENGTH = 15
MIN_IP_ADDR_LENGTH = 7
MAX_NAME_LENGTH = 15
MIN_NAME_LENGTH = 2
CREDITS_LINK = "https://github.com/Winnie-Fred/Connect4/blob/49e342fd87cb3fb82386d59762bbb3f2ccf441f4/credits.md"

# Set volume levels
BACKGROUND_MUSIC_VOLUME = 0.4
GAME_STARTED_BACKGROUND_MUSIC_VOLUME = 0.8

class Connect4:
    POINTS_FOR_WINNING_ONE_ROUND = 10
    connect4game = Connect4Game()
    TEMPORARY_SURFACE_WIDTH, TEMPORARY_SURFACE_HEIGHT = 1600.0, 900.0

    def __init__(self):
        pygame.init()
        pygame.mixer.init()
        self.client = Client()
        self.ID = None
        self.round_over = False
        self.keyboard_interrupt = False
        self.red_marker = colored('O', 'red', attrs=['bold'])
        self.yellow_marker = colored('O', 'yellow', attrs=['bold'])
        
        monitor_size = [pygame.display.Info().current_w, pygame.display.Info().current_h]
        self.screen = pygame.display.set_mode(monitor_size, pygame.FULLSCREEN)
        # self.screen = pygame.display.set_mode((1280, 800))

        width, height = self.screen.get_width(), self.screen.get_height()
        xscale = width / self.TEMPORARY_SURFACE_WIDTH
        yscale = height / self.TEMPORARY_SURFACE_HEIGHT
        self.scale = xscale if xscale < yscale else yscale
        self.default_y_position_for_printing_error = self.TEMPORARY_SURFACE_HEIGHT*0.6667

        # These are the distances from the top of the screen to the top of the scaled surface or simply, 
        # the width and height of the horizontal and vertical black bars on either side of the screen (letterbox)
        # We divide by 2 since scaled image will be blitted at the center of the screen.
        self.top_x_padding = abs(self.screen.get_width() - int(self.TEMPORARY_SURFACE_WIDTH*self.scale)) / 2
        self.top_y_padding = abs(self.screen.get_height() - int(self.TEMPORARY_SURFACE_HEIGHT*self.scale)) / 2

        menu_screen_bg = pygame.image.load('../../images/screen backgrounds/menu screen bg.png').convert_alpha()
        discover_connect4_service_bg = pygame.image.load('../../images/screen backgrounds/discover connect4 service screen bg.png').convert_alpha()
        collect_game_code_screen_bg = pygame.image.load('../../images/screen backgrounds/collect game code screen bg.png').convert_alpha()
        choose_token_screen_bg = pygame.image.load('../../images/screen backgrounds/choose token screen bg.png').convert_alpha()
        collect_name_screen_bg = pygame.image.load('../../images/screen backgrounds/collect name screen bg.png').convert_alpha()
        game_setup_screen_bg = pygame.image.load('../../images/screen backgrounds/game setup screen bg.png').convert_alpha()

        screen_backgrounds = namedtuple("screen_backgrounds", "menu_screen_bg, discover_connect4_service_bg, collect_game_code_screen_bg, choose_token_screen_bg, collect_name_screen_bg, game_setup_screen_bg")  
        self.all_screen_backgrounds = screen_backgrounds(menu_screen_bg, discover_connect4_service_bg, collect_game_code_screen_bg, choose_token_screen_bg, collect_name_screen_bg, game_setup_screen_bg)

        error_sound = pygame.mixer.Sound("../../audio/error.wav")
        winner_sound = pygame.mixer.Sound("../../audio/winner.wav")
        loser_sound = pygame.mixer.Sound("../../audio/loser.wav")
        tie_sound = pygame.mixer.Sound("../../audio/tie.wav")

        game_sounds = namedtuple("game_sounds", "error_sound, winner_sound, loser_sound, tie_sound")
        self.all_game_sounds = game_sounds(error_sound, winner_sound, loser_sound, tie_sound)

        self.loading_simulation_frames = []
        for i in range(1, 19):
            self.loading_simulation_frames.append(pygame.image.load(f'../../images/loading animation/loading animation frame ({i}).png').convert_alpha())

        self.error_frames = []
        for i in range(1, 35):
            self.error_frames.append(pygame.image.load(f'../../images/error frames/frame ({i}).png').convert_alpha())        

        self._reset_game()

    def _reset_game(self):
        self.ID = None
        self.you = ""
        self.opponent = Player(name='', marker='')
        self.player = Player(name='', marker='')
        self.token = None
        self.your_turn = False
        self.level = Level()
        self._reset_for_new_round()

    def _reset_for_new_round(self):
        self.board = Board()
        self.play_again_reply = False
        self.play_again_reply_received = False
        self.opponent_play_again_reply = False
        self.opponent_play_again_reply_received = False
        self.first_player_for_next_round = Player(name='', marker='')
        self.round_over_json = {}

    def _calculate_and_display_final_result(self, players):
        player_one, player_two = players
        winner = None
        result = [
            f"You have {player_one.points} points",
            f"{player_two.name} has {player_two.points} points"
        ]
        if player_one.points > player_two.points:
            result.append("You win!")
            winner = player_one
        elif player_two.points > player_one.points:
            result.append("You lose")
            winner = player_two
        else:
            result.append("Game ends in a tie")
            winner = None
        return result, winner

    def _display_result(self, round_or_game="round"):
        result = []
        if round_or_game == "round":
            result = [
                "At the end of round {self.level.current_level}, ", 
                f"You have {self.player.points} points",
                f"{self.opponent.name} has {self.opponent.points} points"
            ]
        elif round_or_game == "game":
            
            if self.level.current_level == 1:
                result.append(f"At the end of the game,")
            else:
                result.append(f"At the end of the game, after {self.level.current_level} rounds,")
                
            final_result, winner = self._calculate_and_display_final_result([self.player, self.opponent])
            result.extend(final_result)
            result.append("Thanks for playing")
        return result, winner

    def _stop_all_sounds(self):
        for sound in self.all_game_sounds:
            sound.stop()
    
    def run_game(self):
        screen = self.screen
        pygame.display.set_caption("Connect4")
        icon = pygame.image.load('../../images/icon.png').convert_alpha()
        pygame.display.set_icon(icon)

        game_state = GameState.MENU
        
        while True:          

            if game_state == GameState.MENU:
                game_state = self.menu_screen(screen)                

            if game_state == GameState.CREATE_GAME:
                game_state = self.discover_connect4_service_screen(screen, self.main_game_screen, choice=Choice.CREATE_GAME)

            if game_state == GameState.JOIN_ANY_GAME:
                game_state = self.discover_connect4_service_screen(screen, self.main_game_screen, choice=Choice.JOIN_ANY_GAME)

            if game_state == GameState.JOIN_GAME_WITH_CODE:
                game_state = self.discover_connect4_service_screen(screen, self.join_game_with_code_screen)            

            if game_state == GameState.QUIT:                
                pygame.quit()
                return

    def menu_screen(self, screen):
        
        menu_header = create_text_to_draw("Ready to play Connect4?", 30, WHITE, TRANSPARENT, (self.TEMPORARY_SURFACE_WIDTH*0.5, self.TEMPORARY_SURFACE_HEIGHT*0.17))

        create_game_btn = UIElement(
            center_position=(self.TEMPORARY_SURFACE_WIDTH*0.5, self.TEMPORARY_SURFACE_HEIGHT*0.3),
            font_size=25,
            bg_rgb=TRANSPARENT,
            text_rgb=WHITE,
            text="Create a game",
            action=GameState.CREATE_GAME,
        )
        join_any_game_btn = UIElement(
            center_position=(self.TEMPORARY_SURFACE_WIDTH*0.5, self.TEMPORARY_SURFACE_HEIGHT*0.45),
            font_size=25,
            bg_rgb=TRANSPARENT,
            text_rgb=WHITE,
            text="Join any game",
            action=GameState.JOIN_ANY_GAME,
        )
        join_game_with_code_btn = UIElement(
            center_position=(self.TEMPORARY_SURFACE_WIDTH*0.5, self.TEMPORARY_SURFACE_HEIGHT*0.6),
            font_size=25,
            bg_rgb=TRANSPARENT,
            text_rgb=WHITE,
            text="Join game with code",
            action=GameState.JOIN_GAME_WITH_CODE,
        )
        credits_btn = UIElement(
            center_position=(self.TEMPORARY_SURFACE_WIDTH*0.5, self.TEMPORARY_SURFACE_HEIGHT*0.75),
            font_size=25,
            bg_rgb=TRANSPARENT,
            text_rgb=WHITE,
            text="Credits",
            action=GameState.CREDITS,
        )
        quit_btn = UIElement(
            center_position=(self.TEMPORARY_SURFACE_WIDTH*0.5, self.TEMPORARY_SURFACE_HEIGHT*0.9),
            font_size=25,
            bg_rgb=TRANSPARENT,
            text_rgb=WHITE,
            text="Quit",
            action=GameState.QUIT,
        )

        buttons = RenderUpdates(create_game_btn, join_any_game_btn, join_game_with_code_btn, credits_btn, quit_btn)

        return self.game_menu_loop(screen, buttons, menu_header)

    def main_game_screen(self, screen, choice, code=''):                

        red_bird_flying_frames = []
        for i in range(1, 8):
            red_bird_flying_frames.append(pygame.image.load(f'../../images/red bird frames/red-bird-{i}.png').convert_alpha())

        blue_bird_flying_frames = []
        bigger_blue_bird_flying_frames = []
        for i in range(1, 5):
            blue_bird_flying_frames.append(pygame.image.load(f'../../images/blue bird frames/blue-bird-{i}.png').convert_alpha())
            bigger_blue_bird_flying_frames.append(pygame.image.load(f'../../images/bigger blue bird frames/bigger-blue-bird-{i}.png').convert_alpha())

        girl_swinging_frames = []
        for i in range(28):
            girl_swinging_frames.append(pygame.image.load(f'../../images/girl-swinging frames/girl on swing frame ({i}).png').convert_alpha())

        sun_rotating_frames = []
        for i in range(41):
            sun_rotating_frames.append(pygame.image.load(f'../../images/sun frames/Sun frame ({i}).png').convert_alpha())

        sockets_disconnection_simulation_frames = []
        for i in range(18):
            sockets_disconnection_simulation_frames.append(pygame.image.load(f'../../images/sockets disconnection frames/sockets disconnection ({i}).png').convert_alpha())

        frames = namedtuple("frames", "loading_simulation_frames, red_bird_flying_frames, blue_bird_flying_frames, bigger_blue_bird_flying_frames,  girl_swinging_frames, sun_rotating_frames, sockets_disconnection_simulation_frames")  
        all_frames = frames(self.loading_simulation_frames, red_bird_flying_frames, blue_bird_flying_frames, bigger_blue_bird_flying_frames, girl_swinging_frames, sun_rotating_frames, sockets_disconnection_simulation_frames)                

        msg = "Do you want to play another round?"
        texts = []
        texts.append(create_text_to_draw(msg, 20, WHITE, TRANSPARENT, (self.TEMPORARY_SURFACE_WIDTH*0.5, self.TEMPORARY_SURFACE_HEIGHT*0.4)))

        yes_btn = UIElement(
            center_position=(self.TEMPORARY_SURFACE_WIDTH*0.3, self.TEMPORARY_SURFACE_HEIGHT*0.7),
            font_size=20,
            bg_rgb=TRANSPARENT,
            text_rgb=WHITE,
            text="Yes",
            action=GameState.PLAY_AGAIN_YES,
        )
        no_btn = UIElement(
            center_position=(self.TEMPORARY_SURFACE_WIDTH*0.7, self.TEMPORARY_SURFACE_HEIGHT*0.7),
            font_size=20,
            bg_rgb=TRANSPARENT,
            text_rgb=WHITE,
            text="No",
            action=GameState.PLAY_AGAIN_NO,
        )

        return_btn = UIElement(
            center_position=(self.TEMPORARY_SURFACE_WIDTH*0.04, self.TEMPORARY_SURFACE_HEIGHT*0.98),
            font_size=18,
            bg_rgb=TRANSPARENT,
            text_rgb=WHITE,
            text="Quit",
            action=GameState.MENU,
        )

        play_again_screen_buttons = RenderUpdates(return_btn, yes_btn, no_btn)
        play_again_screen = namedtuple("play_again_screen", "buttons, texts")
        play_again_screen_ui = play_again_screen(play_again_screen_buttons, texts)        

        copy_btn = CopyButtonElement(
            center_position=(self.TEMPORARY_SURFACE_WIDTH*0.75, self.TEMPORARY_SURFACE_HEIGHT*0.65),
            font_size=15,
            bg_rgb=TRANSPARENT,
            text_rgb=WHITE,
            text="Copy",
            text_after_mouse_up_event="Copied",
            action=GameState.COPY,
        )

        buttons = RenderUpdates(return_btn)
        copy_btn = RenderUpdates(copy_btn)
        background = pygame.image.load('../../images/playground.png').convert_alpha()
        board = pygame.image.load('../../images/Connect4 Giant set.png').convert_alpha()
        
        return self.play_game(screen, background, board, buttons, copy_btn, all_frames, play_again_screen_ui, choice=choice, code=code)

    def discover_connect4_service_screen(self, screen, next_screen, **kwargs):

        service_found_texts = []
        service_not_found_texts = []

        service_found_texts.append(create_text_to_draw("Connect4 Service found", 20, WHITE, TRANSPARENT, (self.TEMPORARY_SURFACE_WIDTH*0.5, self.TEMPORARY_SURFACE_HEIGHT*0.75)))

        service_not_found_msgs = [
            "Connect4 Service not found. Unable to start game.", 
            "Make sure the server is running and you are connected to the server's local network"
        ]
        
        y_pos = self.default_y_position_for_printing_error
        for msg in service_not_found_msgs:
            service_not_found_texts.append(create_text_to_draw(msg, 18, RED, TRANSPARENT, (self.TEMPORARY_SURFACE_WIDTH*0.5, y_pos)))           
            y_pos += self.TEMPORARY_SURFACE_HEIGHT*0.0833

        return_btn = UIElement(
            center_position=(self.TEMPORARY_SURFACE_WIDTH*0.13, self.TEMPORARY_SURFACE_HEIGHT*0.95),
            font_size=15,
            bg_rgb=TRANSPARENT,
            text_rgb=WHITE,
            text="Return to main menu",
            action=GameState.MENU,
        )
        
        buttons = RenderUpdates(return_btn)
        ui_action = self.discover_connect4_service_loop(screen, buttons=buttons, loading_simulation_frames=self.loading_simulation_frames, service_found_texts=service_found_texts, service_not_found_texts=service_not_found_texts)

        if ui_action == GameState.CONNECT4_SERVICE_FOUND:
            if 'choice' in kwargs:
                choice = kwargs['choice']
                return next_screen(screen, choice=choice)
            return next_screen(screen)
        return ui_action

    def collect_name_screen(self, screen, name=''):
        
        continue_btn = DisabledOrEnabledBtn(
            center_position=(self.TEMPORARY_SURFACE_WIDTH*0.5, self.TEMPORARY_SURFACE_HEIGHT*0.6667),
            font_size=20,
            bg_rgb=TRANSPARENT,
            text_rgb=WHITE,
            grayed_out_text_rgb=GRAY,
            text="Continue",
            action=GameState.SUBMIT_NAME,
        )

        return_btn = UIElement(
            center_position=(self.TEMPORARY_SURFACE_WIDTH*0.13, self.TEMPORARY_SURFACE_HEIGHT*0.95),
            font_size=15,
            bg_rgb=TRANSPARENT,
            text_rgb=WHITE,
            text="Return to main menu",
            action=GameState.MENU,
        )
        
        paste_btn = UIElement(
            center_position=(self.TEMPORARY_SURFACE_WIDTH*0.75, self.TEMPORARY_SURFACE_HEIGHT*0.3333),
            font_size=15,
            bg_rgb=TRANSPARENT,
            text_rgb=WHITE,
            text="Paste",
            action=GameState.PASTE,
        )

        clear_btn = UIElement(
            center_position=(self.TEMPORARY_SURFACE_WIDTH*0.25, self.TEMPORARY_SURFACE_HEIGHT*0.3333),
            font_size=15,
            bg_rgb=TRANSPARENT,
            text_rgb=WHITE,
            text="Clear",
            action=GameState.CLEAR,
        )

        input_box = InputBox(
            center_position = (self.TEMPORARY_SURFACE_WIDTH*0.5, self.TEMPORARY_SURFACE_HEIGHT*0.3333),
            placeholder_text='Enter your name here',
            font_size=20,
            bg_rgb=TRANSPARENT,
            text_rgb=WHITE,
            max_input_length=MAX_NAME_LENGTH,
            min_input_length=MIN_NAME_LENGTH,
        )

        fade_out_text = FadeOutText(
            font_size=15,
            text_rgb=RED,
            bg_rgb=TRANSPARENT,
            center_position=(self.TEMPORARY_SURFACE_WIDTH*0.5, self.TEMPORARY_SURFACE_HEIGHT*0.5))

        buttons = RenderUpdates(return_btn, paste_btn, clear_btn)
        bg = self.all_screen_backgrounds.collect_name_screen_bg
        return self.collect_input_loop(screen, buttons=buttons, submit_input_btn=continue_btn, input_box=input_box, fade_out_text=fade_out_text, bg=bg, name=name)               

    def display_result_screen(self, screen, temp_surf, results=[]):
        texts = []
        result_y = 0.15
        for result in results:
            texts.append(create_text_to_draw(result, 20, WHITE, TRANSPARENT, (self.TEMPORARY_SURFACE_WIDTH*0.5, self.TEMPORARY_SURFACE_HEIGHT*result_y)))
            result_y += 0.1
        

        end_game_btn = UIElement(
            center_position=(self.TEMPORARY_SURFACE_WIDTH*0.5, self.TEMPORARY_SURFACE_HEIGHT*(result_y+0.2)),
            font_size=25,
            bg_rgb=TRANSPARENT,
            text_rgb=WHITE,
            text="End Game",
            action=GameState.MENU,
        )
        return_btn = UIElement(
            center_position=(self.TEMPORARY_SURFACE_WIDTH*0.04, self.TEMPORARY_SURFACE_HEIGHT*0.98),
            font_size=18,
            bg_rgb=TRANSPARENT,
            text_rgb=WHITE,
            text="Quit",
            action=GameState.MENU,
        )

        buttons = RenderUpdates(return_btn, end_game_btn)
        return self.display_result_loop(screen, temp_surf, buttons=buttons, texts=texts)     
    
    def choose_token_screen(self, screen, name=''):
        
        texts = []
        msg = "You go first"
        texts.append(create_text_to_draw(msg, 20, WHITE, TRANSPARENT, (self.TEMPORARY_SURFACE_WIDTH*0.5, self.TEMPORARY_SURFACE_HEIGHT*0.1667)))
        msg = f"{name}, choose a token"
        texts.append(create_text_to_draw(msg, 25, WHITE, TRANSPARENT, (self.TEMPORARY_SURFACE_WIDTH*0.5, self.TEMPORARY_SURFACE_HEIGHT*0.25)))

        inactive_red_button_img = pygame.image.load('../../images/token buttons/inactive red token button.png').convert_alpha()
        mouse_over_red_button_img = pygame.image.load('../../images/token buttons/mouse over red token button.png').convert_alpha()
        inactive_yellow_button_img = pygame.image.load('../../images/token buttons/inactive yellow token button.png').convert_alpha()
        mouse_over_yellow_button_img = pygame.image.load('../../images/token buttons/mouse over yellow token button.png').convert_alpha()
        
        red_token_btn = TokenButton(
            button_img=inactive_red_button_img,
            mouse_over_btn_img=mouse_over_red_button_img,
            top_left_position=(self.TEMPORARY_SURFACE_WIDTH*0.3, self.TEMPORARY_SURFACE_HEIGHT*0.333333),
            action=GameState.SELECT_RED_TOKEN,
        )

        yellow_token_btn = TokenButton(
            button_img=inactive_yellow_button_img,
            mouse_over_btn_img=mouse_over_yellow_button_img,
            top_left_position=(self.TEMPORARY_SURFACE_WIDTH*0.6, self.TEMPORARY_SURFACE_HEIGHT*0.333333),
            action=GameState.SELECT_YELLOW_TOKEN,
        )
       
        return_btn = UIElement(
            center_position=(self.TEMPORARY_SURFACE_WIDTH*0.13, self.TEMPORARY_SURFACE_HEIGHT*0.9533),
            font_size=15,
            bg_rgb=TRANSPARENT,
            text_rgb=WHITE,
            text="Return to main menu",
            action=GameState.MENU,
        )

        buttons = RenderUpdates(return_btn, red_token_btn, yellow_token_btn)
        return self.choose_token_loop(screen, buttons=buttons, texts=texts)

    def join_game_with_code_screen(self, screen):
        
        join_game_btn = DisabledOrEnabledBtn(
            center_position=(self.TEMPORARY_SURFACE_WIDTH*0.5, self.TEMPORARY_SURFACE_HEIGHT*0.6667),
            font_size=20,
            bg_rgb=TRANSPARENT,
            text_rgb=WHITE,
            grayed_out_text_rgb=GRAY,
            text="Join game",
            action=GameState.JOIN_GAME_WITH_ENTERED_CODE,
        )

        return_btn = UIElement(
            center_position=(self.TEMPORARY_SURFACE_WIDTH*0.13, self.TEMPORARY_SURFACE_HEIGHT*0.95),
            font_size=15,
            bg_rgb=TRANSPARENT,
            text_rgb=WHITE,
            text="Return to main menu",
            action=GameState.MENU,
        )
        
        paste_btn = UIElement(
            center_position=(self.TEMPORARY_SURFACE_WIDTH*0.75, self.TEMPORARY_SURFACE_HEIGHT*0.3333),
            font_size=15,
            bg_rgb=TRANSPARENT,
            text_rgb=WHITE,
            text="Paste",
            action=GameState.PASTE,
        )

        clear_btn = UIElement(
            center_position=(self.TEMPORARY_SURFACE_WIDTH*0.25, self.TEMPORARY_SURFACE_HEIGHT*0.3333),
            font_size=15,
            bg_rgb=TRANSPARENT,
            text_rgb=WHITE,
            text="Clear",
            action=GameState.CLEAR,
        )

        input_box = InputBox(
            center_position = (self.TEMPORARY_SURFACE_WIDTH*0.5, self.TEMPORARY_SURFACE_HEIGHT*0.3333),
            placeholder_text='Enter code here',
            font_size=20,
            bg_rgb=TRANSPARENT,
            text_rgb=WHITE,
            max_input_length=GAME_CODE_LENGTH,
            min_input_length=GAME_CODE_LENGTH,
        )

        fade_out_text = FadeOutText(
            font_size=15,
            text_rgb=RED,
            bg_rgb=TRANSPARENT,
            center_position=(self.TEMPORARY_SURFACE_WIDTH*0.5, self.TEMPORARY_SURFACE_HEIGHT*0.5))

        buttons = RenderUpdates(return_btn, paste_btn, clear_btn)
        bg = self.all_screen_backgrounds.collect_game_code_screen_bg
        game_state_and_input = self.collect_input_loop(screen, buttons=buttons, submit_input_btn=join_game_btn, input_box=input_box, fade_out_text=fade_out_text, bg=bg)
        ui_action = game_state_and_input.game_state
        if ui_action != GameState.MENU:
            return self.main_game_screen(screen, choice=Choice.JOIN_GAME_WITH_CODE, code=game_state_and_input.input)
        return ui_action

    def game_menu_loop(self, screen, buttons, menu_header):
        """ Handles game menu loop until an action is return by a button in the
            buttons sprite renderer.
        """

        pygame.mixer.music.stop()
        self._stop_all_sounds()
        pygame.mixer.music.load('../../audio/background music.wav')
        pygame.mixer.music.play(-1)
        pygame.mixer.music.set_volume(BACKGROUND_MUSIC_VOLUME)

        temporary_surface = pygame.Surface((self.TEMPORARY_SURFACE_WIDTH, self.TEMPORARY_SURFACE_HEIGHT))

        if self.client.client is not None:
            try:
                self.client.send_data({'DISCONNECT':self.client.DISCONNECT_MESSAGE})
            except socket.error:
                pass
            self.client.client.close()
        self._reset_game()

        while True:
            mouse_up = False
            for event in pygame.event.get():
                if event.type == pygame.QUIT:
                    pygame.quit()
                    sys.exit()
                if event.type == pygame.MOUSEBUTTONUP and event.button == 1:
                    mouse_up = True            
                
            temporary_surface.blit(self.all_screen_backgrounds.menu_screen_bg, (0, 0))

            scaled_pos = self.get_scaled_mouse_position()

            menu_header.draw(temporary_surface)
            
            for button in buttons:              
                ui_action = button.update(scaled_pos, mouse_up)
                if ui_action != GameState.NO_ACTION:
                    if ui_action == GameState.CREDITS:
                        webbrowser.open(CREDITS_LINK, new=2)
                        pygame.display.iconify() #  Minimize window to show opened browser tab since game runs in fullscreen mode
                    else:
                        return ui_action

            buttons.draw(temporary_surface)

            scaled_surface = pygame.transform.smoothscale(temporary_surface, (int(self.TEMPORARY_SURFACE_WIDTH*self.scale), int(self.TEMPORARY_SURFACE_HEIGHT*self.scale)))     
            screen.blit(scaled_surface, (self.top_x_padding, self.top_y_padding))
            pygame.display.flip()
            clock.tick(60)

    def discover_connect4_service_loop(self, screen, buttons, loading_simulation_frames, service_found_texts, service_not_found_texts):

        temporary_surface = pygame.Surface((self.TEMPORARY_SURFACE_WIDTH, self.TEMPORARY_SURFACE_HEIGHT))
        dimmed_surface = pygame.Surface((self.TEMPORARY_SURFACE_WIDTH, self.TEMPORARY_SURFACE_HEIGHT))
        zeroconf = Zeroconf()
        print("\nSearching for Connect4 Game service...\n\n")
        ServiceBrowser(zeroconf, service_type, self.client)
        start_time = pygame.time.get_ticks()
        timeout = TIMEOUT_FOR_SERVICE_SEARCH * 1000
        search_complete = False

        last_update_of_loading_animation = pygame.time.get_ticks()
        loading_animation_cooldown = 370
        loading_animation_frame = 0         
        
        last_update_of_error_animation = pygame.time.get_ticks()
        error_animation_cooldown = 50
        error_animation_frame = 0         

        start_ticks = None 
        error = False

        while True:
            mouse_up = False
            for event in pygame.event.get():
                if event.type == pygame.QUIT:
                    pygame.quit()
                    sys.exit()
                if event.type == pygame.MOUSEBUTTONUP and event.button == 1:
                    mouse_up = True            
                
            temporary_surface.blit(self.all_screen_backgrounds.discover_connect4_service_bg, (0, 0))

            scaled_pos = self.get_scaled_mouse_position()

            for button in buttons:              
                ui_action = button.update(scaled_pos, mouse_up)
                if ui_action != GameState.NO_ACTION:                    
                    return ui_action

            buttons.draw(temporary_surface)

            if not search_complete:
                current_time = pygame.time.get_ticks()
                if current_time - last_update_of_loading_animation >= loading_animation_cooldown:
                    loading_animation_frame += 1
                    last_update_of_loading_animation = current_time
                    if loading_animation_frame >= len(loading_simulation_frames):
                        loading_animation_frame = 0

                temporary_surface.blit(loading_simulation_frames[loading_animation_frame], (559, 94))
                loading_msg = create_text_to_draw("Searching for Connect4 service", 15, WHITE, TRANSPARENT, (self.TEMPORARY_SURFACE_WIDTH*0.5, self.TEMPORARY_SURFACE_HEIGHT*0.75))
                loading_msg.draw(temporary_surface)

                if not self.client.service_found:
                    elapsed_time = pygame.time.get_ticks() - start_time            
                    if elapsed_time >= timeout:      
                        zeroconf.close()
                        start_ticks = pygame.time.get_ticks()
                        search_complete = True
                        error = True
                        pygame.mixer.music.stop()
                        self._stop_all_sounds()
                        self.all_game_sounds.error_sound.play()
                        print(colored("Connect4 Service not found. Unable to start game. Make sure the server is running and you are connected to the server's local network", "red", attrs=['bold']))
                else:
                    zeroconf.close()
                    start_ticks = pygame.time.get_ticks()
                    search_complete = True
            
            if search_complete:                
                if self.client.service_found:
                    elapsed_ticks = pygame.time.get_ticks() - start_ticks                
                    if elapsed_ticks <= 2000:
                        for text in service_found_texts:
                            text.draw(temporary_surface)
                    else:
                        self.client.service_found = False
                        return GameState.CONNECT4_SERVICE_FOUND
                else:
                    temporary_surface.blit(self.all_screen_backgrounds.game_setup_screen_bg, (0, 0))
                    dimmed_surface.fill((0, 0, 0))
                    dimmed_surface.set_alpha(220)
                    current_time = pygame.time.get_ticks()
                    if current_time - last_update_of_error_animation >= error_animation_cooldown:
                        error_animation_frame += 1
                        last_update_of_error_animation = current_time
                        if error_animation_frame >= len(self.error_frames):
                            error_animation_frame = 0
                    dimmed_surface.blit(self.error_frames[error_animation_frame], (549, 79))
                    for text in service_not_found_texts:
                        text.draw(dimmed_surface)
                    buttons.draw(dimmed_surface)


            
            scaled_surface = pygame.transform.smoothscale(temporary_surface, (int(self.TEMPORARY_SURFACE_WIDTH*self.scale), int(self.TEMPORARY_SURFACE_HEIGHT*self.scale)))     
            screen.blit(scaled_surface, (self.top_x_padding, self.top_y_padding))
            if error:
                scaled_surface = pygame.transform.smoothscale(dimmed_surface, (int(self.TEMPORARY_SURFACE_WIDTH*self.scale), int(self.TEMPORARY_SURFACE_HEIGHT*self.scale)))     
                screen.blit(scaled_surface, (self.top_x_padding, self.top_y_padding))

            pygame.display.flip()
            clock.tick(60)


    def collect_input_loop(self, screen, buttons, input_box, submit_input_btn, fade_out_text, bg, name=''):
        """ Collects input in loop until an action is return by a button in the
            buttons sprite renderer.
        """

        temporary_surface = pygame.Surface((self.TEMPORARY_SURFACE_WIDTH, self.TEMPORARY_SURFACE_HEIGHT))
        
        submit_btn_enabled = False
        error = ''
        returned_input = ''
        alpha = 255  # The current alpha value of the text surface.

        time_until_fade = 4000
        time_of_error = None   
        
        game_state_and_input = namedtuple("game_state_and_input", "game_state, input")

        while True:
            pasted_input = None
            clear = False
            mouse_up = False
            key_down = False
            backspace = False
            enter_key_pressed = False
            pressed_key = None
            validation = None
            for event in pygame.event.get():
                if event.type == pygame.QUIT:
                    pygame.quit()
                    sys.exit()
                if event.type == pygame.MOUSEBUTTONUP and event.button == 1:
                    mouse_up = True
                
                if event.type == pygame.KEYDOWN:
                    key_down = True
                    if event.key == pygame.K_BACKSPACE:
                        backspace = True
                    elif event.key == pygame.K_RETURN:
                        key_down = False
                        enter_key_pressed = True
                    else:
                        pressed_key = event.unicode
                
            temporary_surface.blit(bg, (0, 0))

            scaled_pos = self.get_scaled_mouse_position()

            if error:
                # TODO: Make error fade out after some time, possibly display error when they have entered max characters              

                fade_out_text.text = error
                
                current_time = pygame.time.get_ticks()
                if current_time - time_of_error >= time_until_fade:
                    if alpha > 0:
                        # Reduce alpha each frame, but make sure it doesn't get below 0.
                        alpha = max(alpha-4, 0)

                if not fade_out_text.update(alpha):
                    error = ''
                    alpha = 255
                    
                fade_out_text.draw(temporary_surface)                    
            

            for button in buttons:                
                ui_action = button.update(scaled_pos, mouse_up)
                
                if ui_action != GameState.NO_ACTION:
                    if ui_action == GameState.PASTE:
                        pasted_input = pyperclip.paste()
                    elif ui_action == GameState.CLEAR:
                        clear = True                    
                    else:                                              
                        return game_state_and_input(ui_action, '')

            buttons.draw(temporary_surface)

            
            ui_action = submit_input_btn.update(scaled_pos, mouse_up, submit_btn_enabled, enter_key_pressed)
            if ui_action != GameState.NO_ACTION:
                if ui_action == GameState.JOIN_GAME_WITH_ENTERED_CODE:
                    validation = self.validate_game_code(returned_input)                
                elif ui_action == GameState.SUBMIT_NAME:
                    validation = self.validate_name(returned_input, name)
                if validation is not None:
                    if validation.passed_validation:                        
                        return game_state_and_input(ui_action, validation.valid_input_or_error)
                    else:
                        # Validation failed
                        time_of_error = pygame.time.get_ticks()
                        error = validation.valid_input_or_error                    
                else:
                    return game_state_and_input(ui_action, '')
            submit_input_btn.draw(temporary_surface)

            
            after_input = input_box.update(scaled_pos, mouse_up, key_down, pressed_key, backspace, pasted_input, clear)                   
            input_box.draw(temporary_surface)
            pygame.draw.rect(temporary_surface, after_input.color, (input_box.rect.left-3, input_box.rect.top-5, input_box.rect.w+10, input_box.rect.h*2), 2)
            submit_btn_enabled = after_input.submit_btn_enabled
            returned_input = after_input.returned_input

            scaled_surface = pygame.transform.smoothscale(temporary_surface, (int(self.TEMPORARY_SURFACE_WIDTH*self.scale), int(self.TEMPORARY_SURFACE_HEIGHT*self.scale)))     
            screen.blit(scaled_surface, (self.top_x_padding, self.top_y_padding))
            pygame.display.flip()
            clock.tick(60)
    
    def choose_token_loop(self, screen, buttons, texts):
        """ Collects player's token in loop until an action is return by a button in the
            buttons sprite renderer.
        """

        temporary_surface = pygame.Surface((self.TEMPORARY_SURFACE_WIDTH, self.TEMPORARY_SURFACE_HEIGHT))

        while True:
            mouse_up = False
            for event in pygame.event.get():
                if event.type == pygame.QUIT:
                    pygame.quit()
                    sys.exit()
                if event.type == pygame.MOUSEBUTTONUP and event.button == 1:
                    mouse_up = True
                
            temporary_surface.blit(self.all_screen_backgrounds.choose_token_screen_bg, (0, 0))
            scaled_pos = self.get_scaled_mouse_position()

            for text in texts:
                text.draw(temporary_surface)

            for button in buttons:
                ui_action = button.update(scaled_pos, mouse_up)
                if ui_action != GameState.NO_ACTION:
                    return ui_action

            buttons.draw(temporary_surface)

            scaled_surface = pygame.transform.smoothscale(temporary_surface, (int(self.TEMPORARY_SURFACE_WIDTH*self.scale), int(self.TEMPORARY_SURFACE_HEIGHT*self.scale)))     
            screen.blit(scaled_surface, (self.top_x_padding, self.top_y_padding))
            pygame.display.flip()
            clock.tick(60)

    def play_again_loop(self, screen, temp_surf, buttons, texts):

        pygame.mixer.music.pause()
        
        pause_screen = pygame.Surface((self.TEMPORARY_SURFACE_WIDTH, self.TEMPORARY_SURFACE_HEIGHT))

        while True:
            mouse_up = False
            for event in pygame.event.get():
                if event.type == pygame.QUIT:
                    pygame.quit()
                    sys.exit()
                if event.type == pygame.MOUSEBUTTONUP and event.button == 1:
                    mouse_up = True
                    
            pause_screen.fill((0, 0, 0))
            pause_screen.set_alpha(200)           
            scaled_pos = self.get_scaled_mouse_position()

            for text in texts:
                text.draw(pause_screen)

            for button in buttons:
                ui_action = button.update(scaled_pos, mouse_up)
                if ui_action != GameState.NO_ACTION:
                    return ui_action

            buttons.draw(pause_screen)

            scaled_surface = pygame.transform.smoothscale(temp_surf, (int(self.TEMPORARY_SURFACE_WIDTH*self.scale), int(self.TEMPORARY_SURFACE_HEIGHT*self.scale)))     
            screen.blit(scaled_surface, (self.top_x_padding, self.top_y_padding))
            scaled_surface = pygame.transform.smoothscale(pause_screen, (int(self.TEMPORARY_SURFACE_WIDTH*self.scale), int(self.TEMPORARY_SURFACE_HEIGHT*self.scale)))     
            screen.blit(scaled_surface, (self.top_x_padding, self.top_y_padding))
            pygame.display.flip()
            clock.tick(60)
        
    def display_result_loop(self, screen, temp_surf, buttons, texts):
        return self.play_again_loop(screen, temp_surf, buttons, texts)

    def blit_board(self, surface, board_surface, mouse_pos_on_click, current_mouse_pos, board_dimensions, red_token, yellow_token, tokens, notifiers, glowing_tokens):
        error_occured = False
        waiting_has_begun = False
        glow = True

        board_topleft = board_dimensions.board_topleft
        board_slot_edges = board_dimensions.board_slot_edges
        horizontal_distance_between_first_row_and_screen_edge = board_dimensions.horizontal_distance_between_first_row_and_screen_edge
        vertical_distance_between_first_col_and_screen_edge = board_dimensions.vertical_distance_between_first_col_and_screen_edge
        height_of_hole = board_dimensions.height_of_hole
        width_of_hole = board_dimensions.width_of_hole
        distance_between_rows = board_dimensions.distance_between_rows
        distance_between_cols = board_dimensions.distance_between_cols
        x_pos_on_click, y_pos_on_click = mouse_pos_on_click
        current_x_pos, _ = current_mouse_pos

        board_x = board_topleft[0]
        board_y = board_topleft[1]
        board_topleft = (board_x, board_y)
        play_status = ()        

        if self.your_turn:

            if int(current_x_pos) in range(board_slot_edges[0], board_slot_edges[-1]):
                for i in range(1, len(board_slot_edges)):                    
                    if int(current_x_pos) in range(board_slot_edges[i-1], board_slot_edges[i]):
                        if not self.board.check_if_column_is_full(i-1):
                            surface.blit(self.token, (board_slot_edges[i-1]+8, board_topleft[1]-10)) #  Offset positions so that token is in the center
           

            if x_pos_on_click is not None and y_pos_on_click is not None and int(x_pos_on_click) in range(board_slot_edges[0], board_slot_edges[-1]):
                for i in range(1, len(board_slot_edges)):
                    if int(x_pos_on_click) in range(board_slot_edges[i-1], board_slot_edges[i]):
                        choice = i-1
                        break

                play_status = self.board.play_at_position(self.player, choice)
                if not play_status.status:
                    error_occured = True
                else:
                    self.your_turn = False
                    self.client.send_data({'board':self.board})
                    check_win = self.board.check_win(self.player)
                    if check_win.win_or_not:
                        self.player.points += self.POINTS_FOR_WINNING_ONE_ROUND
                        print(f"You win this round!\n")
                        self.client.send_data({'round_over':True, 'winner':self.player})

                    if self.board.check_tie():
                        print("\nIt's a tie!\n")
                        self.client.send_data({'round_over':True, 'winner':None})
                                         
                    # Make sure error notifier is outside before bringing in status notifier
                    notifiers.error_notifier.current_position = notifiers.error_notifier.outside_position 
                    print(self.board)


        positions_with_created_tokens = [token.position_on_grid for token in tokens]

        for row_num, row in enumerate(self.board.grid, start=1):
            for col_num, token in enumerate(row, start=1):
                if (row_num-1, col_num-1) not in positions_with_created_tokens:
                    token_x_position = int(horizontal_distance_between_first_row_and_screen_edge + (width_of_hole*(col_num-1)) \
                                        + (distance_between_cols*(col_num-1)))

                    token_y_position = int(vertical_distance_between_first_col_and_screen_edge + (height_of_hole*(row_num-1)) \
                                        + (distance_between_rows*(row_num-1)))

                    initial_position = (board_slot_edges[col_num-1]+8, board_topleft[1]-10)
                    
                    
                    if token == self.red_marker:                                             
                        new_token = Token(red_token, self.red_marker, (row_num-1, col_num-1), (token_x_position, token_y_position), initial_position)
                        tokens.add(new_token)
                    elif token == self.yellow_marker:
                        new_token = Token(yellow_token, self.yellow_marker, (row_num-1, col_num-1), (token_x_position, token_y_position), initial_position)
                        tokens.add(new_token)

        for token in tokens:
            token_state = token.update()
            token.draw(surface)
            if token.marker == self.player.marker and token_state == TokenState.JUST_LANDED:
                waiting_has_begun = True
            if token_state == TokenState.FALLING:
                glow = False #  Tokens should only glow when fourth token in a row just/has landed
            

        notifiers.error_notifier.update(error_occured)
        notifiers.error_notifier.draw(surface)

        notifiers.status_notifier.update(waiting_has_begun=waiting_has_begun, opponent_turn=(not self.your_turn and not self.round_over))
        notifiers.status_notifier.draw(surface)

        surface.blit(board_surface, board_topleft)

        if glow:
            glowing_tokens.update()
            glowing_tokens.draw(surface)

        return play_status


    def play_game(self, screen, background, board, buttons, copy_btn, frames, play_again_screen_ui, choice, code=''):

        def clear_screen():
            nonlocal loading_text, texts
            loading_text = ''
            texts = []

        temporary_surface = pygame.Surface((self.TEMPORARY_SURFACE_WIDTH, self.TEMPORARY_SURFACE_HEIGHT))
        dimmed_screen = pygame.Surface((self.TEMPORARY_SURFACE_WIDTH, self.TEMPORARY_SURFACE_HEIGHT))
        pause_screen = pygame.Surface((self.TEMPORARY_SURFACE_WIDTH, self.TEMPORARY_SURFACE_HEIGHT))
        scaled_game_screen_surface = None

        dim = False
        announce_next_round = False
        time_since_next_round_announcement = None
        next_round_msg = []
        show_play_again_screen = False

        loading_simulation_frames = frames.loading_simulation_frames
        red_bird_flying_frames = frames.red_bird_flying_frames
        blue_bird_flying_frames = frames.blue_bird_flying_frames
        bigger_blue_bird_flying_frames = frames.bigger_blue_bird_flying_frames
        girl_swinging_frames = frames.girl_swinging_frames
        sun_rotating_frames = frames.sun_rotating_frames
        sockets_disconnection_simulation_frames = frames.sockets_disconnection_simulation_frames

        last_update_of_loading_animation = pygame.time.get_ticks()
        loading_animation_cooldown = 200
        loading_animation_frame = 0  

        last_update_of_red_bird_flying = pygame.time.get_ticks()
        red_bird_flying_cooldown = 65
        red_bird_flying_frame = 0 
        red_bird_position = self.TEMPORARY_SURFACE_WIDTH*-0.01 
        red_bird_speed = self.TEMPORARY_SURFACE_WIDTH*0.001

        last_update_of_blue_bird_flying = pygame.time.get_ticks()
        blue_bird_flying_cooldown = 100
        blue_bird_flying_frame = 0  
        blue_bird_position = self.TEMPORARY_SURFACE_WIDTH*-0.005
        blue_bird_speed = self.TEMPORARY_SURFACE_WIDTH*0.0008

        last_update_of_bigger_blue_bird_flying = pygame.time.get_ticks()
        bigger_blue_bird_flying_cooldown = 105
        bigger_blue_bird_flying_frame = 0
        bigger_blue_bird_position = self.TEMPORARY_SURFACE_WIDTH*-0.3
        bigger_blue_bird_speed = self.TEMPORARY_SURFACE_WIDTH*0.0015


        last_update_of_girl_swinging = pygame.time.get_ticks()
        girl_swinging_cooldown = 100
        girl_swinging_frame = 0

        last_update_of_sun_rotating = pygame.time.get_ticks()
        sun_rotating_cooldown = 80
        sun_rotating_frame = 0  

        last_update_of_sockets_disconnection_animation = pygame.time.get_ticks()
        sockets_disconnection_animation_cooldown = 100
        sockets_disconnection_animation_frame = 0

        last_update_of_error_animation = pygame.time.get_ticks()
        error_animation_cooldown = 50
        error_animation_frame = 0

        last_click = None
        enabled = False
        time_until_enable = 3000
        time_of_status_msg_display = 1000

        game_started = False   

        errors = []
        loading_text = ''
        status_msg = ''

        general_error_msg = "Server closed the connection or other client may have disconnected"
        something_went_wrong_msg = "Oops! Something went wrong"
        code_to_display = None
        code_to_copy = ''
        unpickled_json = {}
        texts = []
        
        self._reset_game()

        board_topleft = self.TEMPORARY_SURFACE_WIDTH*0.2581, self.TEMPORARY_SURFACE_HEIGHT*0.195
        distance_from_left_bar_to_first_slot = 47
        # A board slot is the curved part on the top of the board where tokens enter from and drop into the board
        width_of_slots = [92, 98, 100, 97, 97, 99, 95]      
        slot_x = int(board_topleft[0]) + distance_from_left_bar_to_first_slot
        board_slot_edges = [slot_x, ]
        for width in width_of_slots:
            slot_x += width
            board_slot_edges.append(slot_x)
        distance_between_left_bar_and_first_column = 10
        width_of_left_bar = 44
        distance_from_top_of_board_to_top_of_first_row_of_holes = 37
        vertical_distance_between_first_col_and_screen_edge = board_topleft[1] + distance_from_top_of_board_to_top_of_first_row_of_holes 
        horizontal_distance_between_first_row_and_screen_edge = board_topleft[0] + distance_between_left_bar_and_first_column + width_of_left_bar
        width_of_hole, height_of_hole = 72, 72
        distance_between_rows, distance_between_cols = 23, 26

        board_dimensions = namedtuple("board_dimensions", "board_topleft, board_slot_edges," 
                                        "horizontal_distance_between_first_row_and_screen_edge, height_of_hole,"
                                        "width_of_hole, vertical_distance_between_first_col_and_screen_edge, distance_between_rows,"
                                        "distance_between_cols")
        board_dimensions = board_dimensions(board_topleft, board_slot_edges, horizontal_distance_between_first_row_and_screen_edge, 
                                            width_of_hole, height_of_hole, vertical_distance_between_first_col_and_screen_edge, 
                                            distance_between_rows, distance_between_cols)

        red_token = pygame.image.load('../../images/red token.png').convert_alpha()
        yellow_token = pygame.image.load('../../images/yellow token.png').convert_alpha()

        tokens = pygame.sprite.Group()
                
        notifiers = ()
        winner = None
        your_scoreboard = None
        opponent_scoreboard = None
        current_round_board = CurrentRoundBoard(WHITE, 40)

        glowing_tokens = pygame.sprite.Group() #  Tokens that will glow when four of them are in a row i.e. a player has won a round
        glowing_timer = 0

        text_and_error = self.client.connect_to_game(choice, code)
        if text_and_error['error']:
            errors.append(text_and_error['text'])
        else: 
            print(text_and_error['text'])
            status_msg = text_and_error['text']
            status_msg_end_time = pygame.time.get_ticks() + time_of_status_msg_display

        game_started_background_music_played = False
        error_sound_played = False

        # Buffer to store received data
        buffer = b''
        data = b''
        client = self.client.client

        while True:
            default_y_position_for_printing_error = self.default_y_position_for_printing_error

            mouse_up = False
            mouse_pos_on_click = (None, None)
            for event in pygame.event.get():
                if event.type == pygame.QUIT:
                    pygame.quit()
                    sys.exit()
                if event.type == pygame.MOUSEBUTTONUP and event.button == 1:
                    mouse_up = True
                if event.type == pygame.MOUSEBUTTONDOWN:
                    # Get scaled x and y positions
                    mouse_pos_on_click = int((event.pos[0] - self.top_x_padding) / self.scale), int((event.pos[1] - self.top_y_padding) / self.scale)                    

            scaled_pos = self.get_scaled_mouse_position()
            temporary_surface.blit(self.all_screen_backgrounds.game_setup_screen_bg, (0, 0))
            try:
                if game_started:

                    if not game_started_background_music_played:
                        pygame.mixer.music.load('../../audio/game started bg music.wav')
                        pygame.mixer.music.play(-1)
                        pygame.mixer.music.set_volume(GAME_STARTED_BACKGROUND_MUSIC_VOLUME)
                        
                        game_started_background_music_played = True

                    temporary_surface.blit(background, (0, 0))                

                    #------------------------------------------------ Animations plus "current round" board ------------------------------------------------#

                    current_time = pygame.time.get_ticks()
                    if current_time - last_update_of_sun_rotating >= sun_rotating_cooldown:
                        sun_rotating_frame += 1
                        last_update_of_sun_rotating = current_time
                        if sun_rotating_frame >= len(sun_rotating_frames):
                            sun_rotating_frame = 0

                    temporary_surface.blit(sun_rotating_frames[sun_rotating_frame], (self.TEMPORARY_SURFACE_WIDTH*0.055, self.TEMPORARY_SURFACE_HEIGHT*0.06))
                    

                    current_time = pygame.time.get_ticks()
                    if current_time - last_update_of_blue_bird_flying >= blue_bird_flying_cooldown:
                        blue_bird_flying_frame += 1
                        last_update_of_blue_bird_flying = current_time
                        if blue_bird_flying_frame >= len(blue_bird_flying_frames):
                            blue_bird_flying_frame = 0

                    temporary_surface.blit(blue_bird_flying_frames[blue_bird_flying_frame], (blue_bird_position, self.TEMPORARY_SURFACE_HEIGHT*0.01))
                    blue_bird_position += blue_bird_speed
                    if blue_bird_position >= self.TEMPORARY_SURFACE_WIDTH*1.1:
                        blue_bird_position = self.TEMPORARY_SURFACE_WIDTH*-0.005


                    current_time = pygame.time.get_ticks()
                    if current_time - last_update_of_bigger_blue_bird_flying >= bigger_blue_bird_flying_cooldown:
                        bigger_blue_bird_flying_frame += 1
                        last_update_of_bigger_blue_bird_flying = current_time
                        if bigger_blue_bird_flying_frame >= len(bigger_blue_bird_flying_frames):
                            bigger_blue_bird_flying_frame = 0

                    temporary_surface.blit(bigger_blue_bird_flying_frames[bigger_blue_bird_flying_frame], (bigger_blue_bird_position, self.TEMPORARY_SURFACE_HEIGHT*0.005))
                    bigger_blue_bird_position += bigger_blue_bird_speed
                    if bigger_blue_bird_position >= self.TEMPORARY_SURFACE_WIDTH*1.1:
                        bigger_blue_bird_position = self.TEMPORARY_SURFACE_WIDTH*-0.03


                    current_time = pygame.time.get_ticks()
                    if current_time - last_update_of_red_bird_flying >= red_bird_flying_cooldown:
                        red_bird_flying_frame += 1
                        last_update_of_red_bird_flying = current_time
                        if red_bird_flying_frame >= len(red_bird_flying_frames):
                            red_bird_flying_frame = 0

                    temporary_surface.blit(red_bird_flying_frames[red_bird_flying_frame], (red_bird_position, self.TEMPORARY_SURFACE_HEIGHT*0.01))
                    red_bird_position += red_bird_speed
                    if red_bird_position >= self.TEMPORARY_SURFACE_WIDTH*1.1:
                        red_bird_position = self.TEMPORARY_SURFACE_WIDTH*-0.01

                    # Draw current round board before swinging girl so it appears to be behind her
                    current_round_board.update(self.level.current_level)
                    current_round_board.draw(temporary_surface)

                    current_time = pygame.time.get_ticks()
                    if current_time - last_update_of_girl_swinging >= girl_swinging_cooldown:
                        girl_swinging_frame += 1
                        last_update_of_girl_swinging = current_time
                        if girl_swinging_frame >= len(girl_swinging_frames):
                            girl_swinging_frame = 0

                    temporary_surface.blit(girl_swinging_frames[girl_swinging_frame], (self.TEMPORARY_SURFACE_WIDTH*0.79, self.TEMPORARY_SURFACE_HEIGHT*0.5))
                    

                    #------------------------------------------------ Animations plus "current round" board ------------------------------------------------#
                                        
                    self.blit_board(temporary_surface, board, mouse_pos_on_click, scaled_pos, board_dimensions, red_token, yellow_token, tokens, notifiers, glowing_tokens)
                    your_scoreboard.update(self.player.points)
                    your_scoreboard.draw(temporary_surface)
                    opponent_scoreboard.update(self.opponent.points)
                    opponent_scoreboard.draw(temporary_surface)

                for button in buttons:
                    ui_action = button.update(scaled_pos, mouse_up)
                    if ui_action != GameState.NO_ACTION:

                        return ui_action                    
                            
                buttons.draw(temporary_surface)                        

                for text in texts:
                    text.draw(temporary_surface)

                if code_to_display in texts:
                    current_time = pygame.time.get_ticks()
                    if last_click is None or current_time - last_click >= time_until_enable:
                        enabled = True
                    for button in copy_btn:
                        ui_action = button.update(scaled_pos, mouse_up, enabled)
                        if ui_action != GameState.NO_ACTION:
                            if ui_action == GameState.COPY:                        
                                last_click = current_time
                                pyperclip.copy(code_to_copy)
                                enabled = False                                                
                    copy_btn.draw(temporary_surface)


                if loading_text or status_msg:
                    current_time = pygame.time.get_ticks()
                    if current_time - last_update_of_loading_animation >= loading_animation_cooldown:
                        loading_animation_frame += 1
                        last_update_of_loading_animation = current_time
                        if loading_animation_frame >= len(loading_simulation_frames):
                            loading_animation_frame = 0

                    temporary_surface.blit(loading_simulation_frames[loading_animation_frame], (559, 94))
                    if loading_text:
                        loading_msg = create_text_to_draw(loading_text, 15, WHITE, TRANSPARENT, (self.TEMPORARY_SURFACE_WIDTH*0.5, self.TEMPORARY_SURFACE_HEIGHT*0.75))
                        loading_msg.draw(temporary_surface)
                

                if self.round_over and game_started:
                    if not self.play_again_reply_received:
                        if glowing_timer > 0:
                            glowing_timer -= pygame.time.get_ticks() - last_ticks
                        else:
                            show_play_again_screen = True
                            if winner == self.player.name:
                                msg = "You win this round"
                            else:
                                msg = f"{winner} wins this round"
                            play_again_screen_ui.texts.append(create_text_to_draw(msg, 20, WHITE, TRANSPARENT, (self.TEMPORARY_SURFACE_WIDTH*0.5, self.TEMPORARY_SURFACE_HEIGHT*0.2)))                          
                    elif self.play_again_reply_received and not self.opponent_play_again_reply_received:
                        waiting_for_reply_text = f"Waiting for {self.opponent.name} to reply"

                        # Dim the screen
                        dim = True  
                        dimmed_screen.fill((0, 0, 0))
                        dimmed_screen.set_alpha(200)
                        current_time = pygame.time.get_ticks()
                        if current_time - last_update_of_loading_animation >= loading_animation_cooldown:
                            loading_animation_frame += 1
                            last_update_of_loading_animation = current_time
                            if loading_animation_frame >= len(loading_simulation_frames):
                                loading_animation_frame = 0

                        dimmed_screen.blit(loading_simulation_frames[loading_animation_frame], (559, 94))
                        loading_msg = create_text_to_draw(waiting_for_reply_text, 15, WHITE, TRANSPARENT, (self.TEMPORARY_SURFACE_WIDTH*0.5, self.TEMPORARY_SURFACE_HEIGHT*0.6667))
                        loading_msg.draw(dimmed_screen)
                        buttons.draw(dimmed_screen)

                    elif self.play_again_reply_received and self.opponent_play_again_reply_received:
                        time_since_next_round_announcement = pygame.time.get_ticks()
                        announce_next_round = True
                        dim = True
                        if self.play_again_reply:
                            if self.opponent_play_again_reply:
                                self.level.current_level += 1
                                self._reset_for_new_round()                            
                                self.round_over = False
                                tokens = pygame.sprite.Group()
                                glowing_tokens = pygame.sprite.Group()
                                # Shuffle the players again before starting next round.
                                if not self.ID:
                                    first_player = self.connect4game._shuffle_players([self.player, self.opponent])[0]
                                    self.client.send_data({'first_player':first_player})
                            else:
                                final_result = [f"{self.opponent.name} has quit"]
                                result, winner = self._display_result("game")
                                final_result.extend(result)
                                if winner == None:
                                    self.all_game_sounds.tie_sound.play()
                                elif winner.name == self.player.name:
                                    self.all_game_sounds.winner_sound.play()
                                else:
                                    self.all_game_sounds.loser_sound.play()
                                ui_action = self.display_result_screen(screen, temporary_surface, final_result)
                                if ui_action == GameState.MENU:
                                    return ui_action   
                                                                                    
                if not errors and not status_msg:


                    last_ticks = pygame.time.get_ticks()

                    # Get the list of sockets which are readable
                    read_sockets, _, _ = select.select([client] , [], [], 0)
                    for sock in read_sockets:
                        # incoming message from remote server
                        if sock == client:
                            try:
                                data = client.recv(16)
                            except ConnectionResetError: #  This exception is caught when the client tries to receive a msg from a disconnected server
                                error = "Connection Reset: Server closed the connection or other client may have disconnected"
                                errors.append(error)
                                continue
                            except socket.error:
                                errors.append(general_error_msg)                                
                                continue
                            
                            if not data: #  This breaks out of the loop when disconnect msg has been sent to server and/or client conn has been closed server-side
                                error_msg = ''
                                
                                if not self.keyboard_interrupt: 
                                    # Connection was forcibly closed by server
                                    error_msg = general_error_msg

                                errors.append(error_msg)
                                continue
                            
                            # Add received data to the buffer
                            buffer += data                            

                            # Process complete messages
                            while len(buffer) >= self.client.HEADERSIZE:
                                # Extract the header and determine the message length
                                header = buffer[:self.client.HEADERSIZE]
                                message_length = int(header)

                                # Check if the complete message is available in the buffer
                                if len(buffer) >= self.client.HEADERSIZE + message_length:

                                    # -------------------------------------Use unpickled json data here-------------------------------------

                                    # Extract the complete message
                                    message = buffer[self.client.HEADERSIZE:self.client.HEADERSIZE + message_length]
                                    unpickled_json = pickle.loads(message) 
                                    print("unpickled_json", unpickled_json)

                                    loading_text = ''                    

                                    if "code" in unpickled_json:
                                        code_to_copy = unpickled_json['code']
                                        code_to_display = create_text_to_draw(code_to_copy, 30, WHITE, TRANSPARENT, (self.TEMPORARY_SURFACE_WIDTH*0.5, self.TEMPORARY_SURFACE_HEIGHT*0.65))
                                        texts.append(code_to_display)
                                        msg = "This is your special code. Send it to someone you wish to join this game."
                                        texts.append(create_text_to_draw(msg, 15, WHITE, TRANSPARENT, (self.TEMPORARY_SURFACE_WIDTH*0.5, self.TEMPORARY_SURFACE_HEIGHT*0.70)))
                                    elif "no_games_found" in unpickled_json:
                                        # Result from unpickled_json is not used because it is too long and has to be broken 
                                        # to be printed on multiple lines                                    
                                        errors = ["No games exist with that code.", "Ask for an up-to-date code, "
                                                    "try creating your own game ", "or try joining a different game"]
                                    elif "game_full" in unpickled_json:
                                        errors.append(unpickled_json['game_full'])
                                    elif "join_successful" in unpickled_json:
                                        status_msg = unpickled_json['join_successful']
                                        status_msg_end_time = pygame.time.get_ticks() + time_of_status_msg_display
                                        print(status_msg)
                                    elif "id" in unpickled_json:
                                        clear_screen()
                                        self.ID = unpickled_json["id"]
                                        status_msg = "Both clients connected. Starting game"                                                                                               
                                        status_msg_end_time = pygame.time.get_ticks() + time_of_status_msg_display
                                        print(status_msg)
                                    elif "is_alive" in unpickled_json:
                                        self.client.send_data({'is_alive':True})
                                    elif "other_client_disconnected" in unpickled_json:                       
                                        errors.append(unpickled_json['other_client_disconnected'])
                                    elif 'timeout' in unpickled_json:
                                        error = unpickled_json['timeout']
                                        print(self.color_error_msg_red(error))
                                        errors.append(error)
                                    elif "status" in unpickled_json:                                                                             
                                        loading_text = unpickled_json['status']                                
                                    elif "waiting_for_name" in unpickled_json:
                                        loading_text = unpickled_json['waiting_for_name']                        
                                    elif "get_first_player_name" in unpickled_json:                                                               
                                        loading_text = "Waiting for other player to enter their name"
                                        game_state_and_input = self.collect_name_screen(screen)
                                        if game_state_and_input.game_state == GameState.MENU:
                                            return game_state_and_input.game_state              
                                        self.you = game_state_and_input.input
                                        self.client.send_data({'you':self.you})
                                    elif "opponent" in unpickled_json:                                                             
                                        self.opponent = unpickled_json['opponent']
                                        if not self.you:
                                            game_state_and_input = self.collect_name_screen(screen, name=self.opponent)
                                            if game_state_and_input.game_state == GameState.MENU:
                                                return game_state_and_input.game_state           
                                            self.you = game_state_and_input.input
                                            self.client.send_data({'you':self.you})                        
                                        print("You are up against: ", self.opponent)                        
                                        # Shuffling player names
                                        if not self.ID:
                                            first_player = self.connect4game._shuffle_players([self.you, self.opponent])
                                            self.client.send_data({'first':first_player})
                                        print("Randomly choosing who to go first . . .")                                                        
                                    elif "first" in unpickled_json:
                                        first = unpickled_json['first'][0]                                                                        
                                        if first == self.you:
                                            msg = f"You go first"
                                            ui_action = self.choose_token_screen(screen, name=self.you)
                                            if ui_action == GameState.MENU:
                                                return ui_action
                                            if ui_action == GameState.SELECT_RED_TOKEN:
                                                colors = ('red', 'yellow')
                                            else:
                                                colors = ('yellow', 'red')
                                            self.client.send_data({'colors':colors})
                                        else:
                                            msg = f"{first} goes first"
                                            texts.append(create_text_to_draw(msg, 15, WHITE, TRANSPARENT, (self.TEMPORARY_SURFACE_WIDTH*0.5, self.TEMPORARY_SURFACE_HEIGHT*0.4167)))
                                            loading_text = f"Waiting for {self.opponent} to choose their token"
                                        print(msg)
                                    elif "colors" in unpickled_json:                                                                                     
                                        colors = unpickled_json['colors']                        
                                        if first == self.you:
                                            self.your_turn = True
                                            if colors[0] == 'red':
                                                self.token = red_token
                                            elif colors[0] == 'yellow':
                                                self.token = yellow_token
                                            self.player = Player(self.you, colored('O', colors[0], attrs=['bold']))                                                                 
                                        else:
                                            self.your_turn = False
                                            if colors[1] == 'red':
                                                self.token = red_token
                                            elif colors[1] == 'yellow':
                                                self.token = yellow_token
                                            self.player = Player(self.you, colored('O', colors[1], attrs=['bold']))                        
                                        self.client.send_data({'opponent_player_object':self.player})
                                    elif "opponent_player_object" in unpickled_json:
                                        self.round_over = False
                                        game_started = True
                                        clear_screen()
                                        self.opponent = unpickled_json['opponent_player_object']
                                        notifiers = namedtuple("notifiers", "error_notifier, status_notifier")
                                        error_notifier = ErrorNotifier("That column is full", 15, WHITE)
                                        status_notifier = StatusNotifier(self.opponent.name, 17, WHITE)
                                        color = 'red' if self.player.marker == self.red_marker else 'yellow'
                                        opponent_color = 'red' if self.player.marker == self.yellow_marker else 'yellow'
                                        your_scoreboard = ScoreBoard(color, WHITE, 15)
                                        opponent_scoreboard = ScoreBoard(opponent_color, WHITE, 15, self.opponent.name)
                                        if not self.your_turn:
                                            status_notifier.incoming = True
                                        notifiers = notifiers(error_notifier, status_notifier)
                                        self._reset_for_new_round()
                                    elif "board" in unpickled_json:
                                        self.board = unpickled_json['board']                                        
                                        self.your_turn = True
                                        check_win = self.board.check_win(self.opponent)
                                        if check_win.win_or_not:
                                            self.your_turn = False
                                    elif "round_over" in unpickled_json and "winner" in unpickled_json:
                                        round_over_json = unpickled_json
                                        self.round_over = True
                                        self.your_turn = False
                                        winner = round_over_json['winner']
                                        glowing_timer = 5000
                                        if winner is not None:
                                            win_check_result = self.board.check_win(winner)
                                            four_in_a_row = win_check_result.four_in_a_row

                                            marker = win_check_result.marker
                                            token_color = "red" if marker == self.red_marker else "yellow"
                                            for position in four_in_a_row:
                                                token_x_position = int(horizontal_distance_between_first_row_and_screen_edge + (width_of_hole*(position[1])) \
                                                                    + (distance_between_cols*(position[1])))

                                                token_y_position = int(vertical_distance_between_first_col_and_screen_edge + (height_of_hole*(position[0])) \
                                                                    + (distance_between_rows*(position[0])))

                                                glowing_token = GlowingToken(token_color, (token_x_position, token_y_position))
                                                glowing_tokens.add(glowing_token)

                                            if winner.name != self.player.name:
                                                print(f"\n{self.opponent.name} {self.opponent.marker} wins this round")
                                                print("Better luck next time!\n")
                                                self.opponent.points = round_over_json['winner'].points
                                               
                                            winner =  winner.name
                                        else:
                                            winner = ''
                                    elif 'play_again' in unpickled_json:
                                        self.opponent_play_again_reply = unpickled_json['play_again']
                                        self.opponent_play_again_reply_received = True                                                      
                                    elif 'first_player' in unpickled_json:
                                        self.first_player_for_next_round = unpickled_json['first_player']
                                        next_round_msg = [f"Round {self.level.current_level}", f"{self.first_player_for_next_round.name} goes first"]
                                        print('\n'.join(next_round_msg))                                                                                                                   

                                    # -------------------------------------Use unpickled json data here-------------------------------------
                                    # Remove the processed message from the buffer
                                    buffer = buffer[self.client.HEADERSIZE + message_length:]
                                else:
                                    # Incomplete message, break out of the loop and wait for more data
                                    break
                        
            
            except socket.error:
                if not self.keyboard_interrupt:
                    print(self.color_error_msg_red(general_error_msg))
                    errors.append(general_error_msg)
                    continue
            except Exception as e: # Catch EOFError and other exceptions
                print(self.color_error_msg_red(e))
                if not self.keyboard_interrupt:
                    print(self.color_error_msg_red(something_went_wrong_msg))
                    errors.append(something_went_wrong_msg)
                    continue

            if status_msg:
                current_time = pygame.time.get_ticks()
                if current_time < status_msg_end_time:
                    loading_msg = create_text_to_draw(status_msg, 15, WHITE, TRANSPARENT, (self.TEMPORARY_SURFACE_WIDTH*0.5, self.TEMPORARY_SURFACE_HEIGHT*0.70))
                    loading_msg.draw(temporary_surface)
                else:
                    status_msg = ''

            if announce_next_round:
                dimmed_screen.fill((0, 0, 0))
                dimmed_screen.set_alpha(200)
                current_time = pygame.time.get_ticks()
                next_round_msg_y_pos = 0.3
                for msg in next_round_msg:
                    loading_msg = create_text_to_draw(msg, 15, WHITE, TRANSPARENT, (self.TEMPORARY_SURFACE_WIDTH*0.5, self.TEMPORARY_SURFACE_HEIGHT*next_round_msg_y_pos))
                    loading_msg.draw(dimmed_screen)
                    next_round_msg_y_pos += 0.3
                buttons.draw(dimmed_screen)
                
                if time_since_next_round_announcement is not None and pygame.time.get_ticks() - time_since_next_round_announcement >= 3000:
                    announce_next_round = False
                    dim = False
                    pygame.mixer.music.unpause()
                    if self.first_player_for_next_round.name == self.player.name:
                        self.your_turn = True                            
                    else:                            
                        self.your_turn = False
                        status_notifier.incoming = True

            if show_play_again_screen:                    
                pygame.mixer.music.pause()
                                            
                pause_screen.fill((0, 0, 0))
                pause_screen.set_alpha(200)    

                for text in play_again_screen_ui.texts:
                    text.draw(pause_screen)

                for button in play_again_screen_ui.buttons:
                    ui_action = button.update(scaled_pos, mouse_up)                        
                    if ui_action == GameState.MENU:
                        return ui_action
                    if ui_action == GameState.PLAY_AGAIN_YES:
                        self.play_again_reply = True
                        self.play_again_reply_received = True
                        show_play_again_screen = False
                        self.client.send_data({'play_again':True})
                    elif ui_action == GameState.PLAY_AGAIN_NO:
                        self.play_again_reply = False
                        self.play_again_reply_received = True
                        show_play_again_screen = False
                        self.client.send_data({'play_again':False})
                        result, winner = self._display_result("game")
                        if winner == None:
                            self.all_game_sounds.tie_sound.play()
                        elif winner.name == self.player.name:
                            self.all_game_sounds.winner_sound.play()
                        else:
                            self.all_game_sounds.loser_sound.play()
                        ui_action = self.display_result_screen(screen, temporary_surface, result)                                                        
                        if ui_action == GameState.MENU:
                            return ui_action 

                play_again_screen_ui.buttons.draw(pause_screen)


            if errors:
                if not error_sound_played:
                    pygame.mixer.music.stop()
                    self._stop_all_sounds()
                    self.all_game_sounds.error_sound.play()
                    error_sound_played = True

                if game_started:
                    if self.round_over and not self.opponent_play_again_reply:
                        errors = [] #  Remove any errors that might have occured in order to display result screen
                    else:
                        screen.blit(scaled_game_screen_surface, (self.top_x_padding, self.top_y_padding))
                else:
                    temporary_surface.blit(self.all_screen_backgrounds.game_setup_screen_bg, (0, 0))
                    scaled_game_screen_surface = pygame.transform.smoothscale(temporary_surface, (int(self.TEMPORARY_SURFACE_WIDTH*self.scale), int(self.TEMPORARY_SURFACE_HEIGHT*self.scale)))
                    screen.blit(scaled_game_screen_surface, (self.top_x_padding, self.top_y_padding))
                
                dimmed_screen.fill((25, 25, 25)) #  (0, 0, 0) is not used because image of disconnected sockets has parts colored with (0, 0, 0) too such as the cord
                dimmed_screen.set_alpha(230)

                for error in errors:
                    error_text = create_text_to_draw(error, 18, RED, TRANSPARENT, (self.TEMPORARY_SURFACE_WIDTH*0.5, default_y_position_for_printing_error))
                    error_text.draw(dimmed_screen)
                    default_y_position_for_printing_error += self.TEMPORARY_SURFACE_HEIGHT*0.0833

                buttons.draw(dimmed_screen)

                current_time = pygame.time.get_ticks()

                if something_went_wrong_msg not in errors:
                    if current_time - last_update_of_sockets_disconnection_animation >= sockets_disconnection_animation_cooldown:
                        sockets_disconnection_animation_frame += 1
                        last_update_of_sockets_disconnection_animation = current_time
                        if sockets_disconnection_animation_frame >= len(sockets_disconnection_simulation_frames):
                            sockets_disconnection_animation_frame = 0
                    dimmed_screen.blit(sockets_disconnection_simulation_frames[sockets_disconnection_animation_frame], (0,0))
                else:                    
                    if current_time - last_update_of_error_animation >= error_animation_cooldown:
                        error_animation_frame += 1
                        last_update_of_error_animation = current_time
                        if error_animation_frame >= len(self.error_frames):
                            error_animation_frame = 0
                    dimmed_screen.blit(self.error_frames[error_animation_frame], (549, 79))

                error_surface = pygame.transform.smoothscale(dimmed_screen, (int(self.TEMPORARY_SURFACE_WIDTH*self.scale), int(self.TEMPORARY_SURFACE_HEIGHT*self.scale)))     
                screen.blit(error_surface, (self.top_x_padding, self.top_y_padding))

            if not errors:
                if not dim:              
                    if show_play_again_screen:
                        screen.blit(scaled_game_screen_surface, (self.top_x_padding, self.top_y_padding))
                        scaled_surface = pygame.transform.smoothscale(pause_screen, (int(self.TEMPORARY_SURFACE_WIDTH*self.scale), int(self.TEMPORARY_SURFACE_HEIGHT*self.scale)))     
                        screen.blit(scaled_surface, (self.top_x_padding, self.top_y_padding))
                    else:
                        scaled_game_screen_surface = pygame.transform.smoothscale(temporary_surface, (int(self.TEMPORARY_SURFACE_WIDTH*self.scale), int(self.TEMPORARY_SURFACE_HEIGHT*self.scale)))
                        screen.blit(scaled_game_screen_surface, (self.top_x_padding, self.top_y_padding))
                else:                    
                    screen.blit(scaled_game_screen_surface, (self.top_x_padding, self.top_y_padding))
                    scaled_surface = pygame.transform.smoothscale(dimmed_screen, (int(self.TEMPORARY_SURFACE_WIDTH*self.scale), int(self.TEMPORARY_SURFACE_HEIGHT*self.scale)))     
                    screen.blit(scaled_surface, (self.top_x_padding, self.top_y_padding))
            
            pygame.display.flip()
            clock.tick(60)
            
    def get_scaled_mouse_position(self):
        pos = list(pygame.mouse.get_pos())
        
        # The mouse position has to be scaled too
        # To get the scaled mouse position relative to the scaled surface and not (0, 0) of the display, 
        # the paddings are subtracted before scaling
        return ((pos[0] - self.top_x_padding) / self.scale), ((pos[1] - self.top_y_padding) / self.scale)

    def color_error_msg_red(self, msg):
        return colored(msg, "red", attrs=['bold'])

    def validate_game_code(self, returned_input):
        validation = namedtuple("validation", "passed_validation, valid_input_or_error") 
        if not returned_input:
            return validation(False, "You must type in a code to continue")
        # Greater than condition is not checked because input box already prevents input greater than the GAME_CODE_LENGTH
        if len(returned_input) < GAME_CODE_LENGTH:
            return validation(False, "Invalid code. Not enough characters")        
        if returned_input.isalnum():
            return validation(True, returned_input)
        return validation(False, "Code may only contain letters and digits")

    def validate_name(self, returned_input, name):
        validation = namedtuple("validation", "passed_validation, valid_input_or_error")
        if not returned_input:
            return validation(False, "You must type in a name to continue")
        # Greater than condition is not checked because input box already prevents input greater than the MAX_NAME_LENGTH
        if len(returned_input) < MIN_NAME_LENGTH:
            return validation(False, "That name is too short")
        if returned_input.isalnum():            
            if returned_input.lower() == name.lower():
                return validation(False, "That name is already taken by the other player. Choose another name")
            return validation(True, returned_input)
        return validation(False, "Your name may only contain letters and digits")

    def terminate_program(self):
        self.keyboard_interrupt = True
        if self.client.client is not None:
            try:
                self.client.send_data({'DISCONNECT':self.client.DISCONNECT_MESSAGE, 'close_other_client':True})
            except socket.error:
                pass
            self.client.client.close()   
        error_msg = colored(f"Keyboard Interrupt: Program ended", "red", attrs=['bold'])        
        print(f"\n{error_msg}\n")
        pygame.quit()        

if __name__ == "__main__":
    connect4 = Connect4()
    try:
        connect4.run_game()        
    except KeyboardInterrupt:
        connect4.terminate_program()