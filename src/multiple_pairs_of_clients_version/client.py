import os
import os
import sys
import time
import itertools
import socket
import pickle
from threading import Thread, Event

from termcolor import colored  # type: ignore
from tabulate import tabulate  # type: ignore
from zeroconf import Zeroconf, ServiceBrowser, ServiceListener

from basic_version.connect4 import Connect4Game
from core.config import service_type, TIMEOUT_FOR_SERVICE_SEARCH
from core.player import Player
from one_pair_of_clients_version.client import Client as BaseClient

os.system('') # To ensure that escape sequences work, and coloured text is displayed normally and not as weird characters

class Client(BaseClient):

    connect4game = Connect4Game()
    def connect_to_game(self):
        # ! self.client must be initialized first before collecting input so that client.send() in Keyboard Interrupt handler does not 
        # ! raise Exception in client.terminate_program()

        try:
            self.client = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        except socket.error as e:
            print(colored(f"Error creating socket: {e}", "red", attrs=['bold']))
            self.stop_flag.set()
            self.connect_again.clear()
            return
        options = [['1.', 'Create a game', '-', 'You can invite someone to play against you with a code'], ['2.', 'Join a game', '-', 'You can join a particular game using a code or any existing game to play against anybody'], ['3.', 'Quit']]
        menu = (f"{'GAME MENU'.center(100, '-')}\n"
                f"{tabulate(options, tablefmt='plain', disable_numparse=True)}"
                )
        while True:
            try:
                choice = int(input(f"\n{menu}\nReady to play Connect4?\nChoose an option from the menu between 1 and 3: ").strip())
                if not choice in range(1, 4):
                    raise ValueError("\nYou must choose an option from the menu between 1 and 3\n")
            except ValueError:
                print(colored("\nYou must choose an option from the menu between 1 and 3\n", "red", attrs=['bold']))
                continue       
            except EOFError:
                print(colored(f"Oops! Something went wrong", "red", attrs=['bold']))
                self.stop_flag.set()
                self.connect_again.clear()
                return
            else:
                break
        
        print("\n")
        if choice == 3:
            print(f"Goodbye\n")
            self.stop_flag.set()
            self.connect_again.clear()
            return
        elif choice == 2:
            try:
                code = input("\nType in the code for the game you want to join or press Enter to join any game: ").strip()
            except EOFError:
                print(colored(f"Oops! Something went wrong", "red", attrs=['bold']))
                self.stop_flag.set()
                self.connect_again.clear()
                return

        try:
            self.client.connect(self.addr)
        except socket.gaierror as e:
            print(colored(f"Address-related error connecting to server: {e}", "red", attrs=['bold']))
            self.client.close()
            self.stop_flag.set()
            self.connect_again.clear()
        except socket.error as e:
            print(colored(f"Connection error: {e}", "red", attrs=['bold']))
            self.client.close()
            self.stop_flag.set()
            self.connect_again.clear()
        else:
            self._reset_game()

            if choice == 1:
                self.send_data({'create_game':'True'})
                loading_msg = "Creating game"
                with self.unpickled_json_lock:
                    loading_thread = Thread(target=self.simulate_loading_with_spinner, args=(loading_msg, self.unpickled_json, ))
                loading_thread.daemon = True
                loading_thread.start()
            elif choice == 2:
                if code:
                    self.send_data({'join_game_with_invite':code})
                else:
                    self.send_data({'join_any_game':'True'})

                loading_msg = "Searching for game to join"
                with self.unpickled_json_lock:
                    loading_thread = Thread(target=self.simulate_loading_with_spinner, args=(loading_msg, self.unpickled_json, ))
                loading_thread.daemon = True
                loading_thread.start()

            play_game_thread = Thread(target=self.play_game)
            play_game_thread.daemon = True
            play_game_thread.start()
            

    def simulate_loading_with_spinner(self, loading_msg, unpickled_json):

        # The simulate_loading_with_spinner thread could have multiple instances running at once, 
        # this makes sure that subsequent instance waits for preceding instance to finish before continuing
        self.spinner_thread_complete.wait() 

        self.spinner_thread_complete.clear()
        NO_OF_CHARACTERS_AFTER_LOADING_MSG = 5
        spaces_to_replace_spinner = ' ' * NO_OF_CHARACTERS_AFTER_LOADING_MSG

        def color_final_loading_msg():
            condition_for_red_msg = ('other_client_disconnected' in self.unpickled_json \
                                    or 'timeout' in self.unpickled_json or 'no_games_found' in self.unpickled_json \
                                    or 'game_full' in self.unpickled_json)
            if type(loading_msg) == list:                
                if condition_for_red_msg or self.end_thread_event.is_set():
                    red_first_part = colored(loading_msg[0], "red", attrs=['bold'])
                    red_last_part = colored(loading_msg[2], "red", attrs=['bold'])
                    final_loading_msg = f"{red_first_part}{loading_msg[1]}{red_last_part}"                
                else:
                    green_first_part = colored(loading_msg[0], "green", attrs=['bold'])
                    green_last_part = colored(loading_msg[2], "green", attrs=['bold'])
                    final_loading_msg = f"{green_first_part}{loading_msg[1]}{green_last_part}"
            else:
                if condition_for_red_msg or self.end_thread_event.is_set():
                    final_loading_msg = colored(loading_msg, "red", attrs=['bold'])
                else:
                    final_loading_msg = colored(loading_msg, "green", attrs=['bold'])
            return final_loading_msg

        if type(loading_msg) == list:
            yellow_first_part = colored(loading_msg[0], "yellow", attrs=['bold'])
            yellow_last_part = colored(loading_msg[2], "yellow", attrs=['bold'])
            yellow_loading_msg = f"{yellow_first_part}{loading_msg[1]}{yellow_last_part}"
        else:
            yellow_loading_msg = colored(loading_msg, "yellow", attrs=['bold'])
            
                
        for c in itertools.cycle(['|', '/', '-', '\\']):
            with self.unpickled_json_lock:         
                if unpickled_json != self.unpickled_json or self.end_thread_event.is_set():
                    sys.stdout.write(f'\r{color_final_loading_msg()}{spaces_to_replace_spinner}')
                    print("\n")
                    break
            sys.stdout.write(f'\r{yellow_loading_msg}  {c}  ')
            sys.stdout.flush()
            time.sleep(0.1)
        self.spinner_thread_complete.set()

    def play_game(self):

        self.play_game_thread_complete.clear()
        general_error_msg = colored(f"Server closed the connection or other client may have disconnected", "red", attrs=['bold'])
        something_went_wrong_msg = colored(f"Oops! Something went wrong", "red", attrs=['bold'])
        buffer = b""

        receiving = True
        while receiving:
            try:
                msg = self.client.recv(16)
            except ConnectionResetError: #  This exception is caught when the client tries to receive a msg from a disconnected server
                error_msg = colored(f"Connection Reset: Server closed the connection or other client may have disconnected", "red", attrs=['bold'])
                self._set_up_to_terminate_program(error_msg)
                break
            except socket.error:
                self._set_up_to_terminate_program(general_error_msg)
                break
            
            if not msg: #  This breaks out of the loop when disconnect msg has been sent to server and/or client conn has been closed server-side
                error_msg = ''
                
                if not self.keyboard_interrupt_event.is_set() and not self.game_ended.is_set() and not self.game_over_event.is_set(): 
                    # Connection was forcibly closed by server
                    error_msg = general_error_msg

                self._set_up_to_terminate_program(error_msg)
                break
            
            # Add received data to the buffer
            buffer += msg 

            # Process complete messages
            while len(buffer) >= self.HEADERSIZE:
                # Extract the header and determine the message length
                header = buffer[:self.HEADERSIZE]
                message_length = int(header)

                # Check if the complete message is available in the buffer
                if len(buffer) >= self.HEADERSIZE + message_length:

                    # -------------------------------------Use unpickled json data here-------------------------------------

                    # Extract the complete message
                    message = buffer[self.HEADERSIZE:self.HEADERSIZE + message_length]
                    with self.unpickled_json_lock:
                        self.unpickled_json = pickle.loads(message) 

                                
                    # ! Wait for simulate_loading_with_spinner thread to complete only after unpickled "message" 
                    # ! is assigned to self.unpickled_json. Otherwise, condition for termination of spinner thread may not be met

                    self.spinner_thread_complete.wait() #  Wait for simulate_loading_with_spinner thread to complete

                    try:
                        self.unpickled_json_lock.acquire()
                        # print("unpickled_json", self.unpickled_json)
                        if "code" in self.unpickled_json:
                            print(f"This is your special code: {self.unpickled_json['code']}\nSend it to someone you wish to join this game.")
                            self.unpickled_json_lock.release()
                        elif "no_games_found" in self.unpickled_json:
                            print(colored(self.unpickled_json['no_games_found'], "red", attrs=['bold']))
                            self.unpickled_json_lock.release()
                            receiving = False
                            break
                        elif "game_full" in self.unpickled_json:
                            print(colored(self.unpickled_json['game_full'], "red", attrs=['bold']))
                            self.unpickled_json_lock.release()
                            receiving = False
                            break
                        elif "join_successful" in self.unpickled_json:
                            print(colored(self.unpickled_json['join_successful'], "green", attrs=['bold']))
                            self.unpickled_json_lock.release()
                        elif "id" in self.unpickled_json:
                            self.ID = self.unpickled_json["id"]
                            loading_msg = "Both clients connected. Starting game"
                            loading_thread = Thread(target=self.simulate_loading_with_spinner, args=(loading_msg, self.unpickled_json, ))
                            self.unpickled_json_lock.release()
                            loading_thread.daemon = True
                            loading_thread.start() 
                        elif "is_alive" in self.unpickled_json:
                            self.unpickled_json_lock.release()
                            self.send_data({'is_alive':True})                                                                       
                        elif "other_client_disconnected" in self.unpickled_json:
                            self.other_client_disconnected.set()
                            disconnect_msg = colored(self.unpickled_json['other_client_disconnected'], "red", attrs=['bold'])
                            self.unpickled_json_lock.release()
                            with self.condition:
                                self.condition.notify()                        
                            self._set_up_to_terminate_program(disconnect_msg)
                            receiving = False
                            break
                        elif "status" in self.unpickled_json:                                                          
                            loading_msg = self.unpickled_json['status'] 
                            loading_thread = Thread(target=self.simulate_loading_with_spinner, args=(loading_msg, self.unpickled_json, ))
                            self.unpickled_json_lock.release()
                            loading_thread.daemon = True
                            loading_thread.start()               
                        elif "waiting_for_name" in self.unpickled_json:
                            loading_msg = self.unpickled_json['waiting_for_name']                                        
                            loading_thread = Thread(target=self.simulate_loading_with_spinner, args=(loading_msg, self.unpickled_json, ))
                            self.unpickled_json_lock.release()
                            loading_thread.daemon = True
                            loading_thread.start()
                        elif "get_first_player_name" in self.unpickled_json:                                                               
                            self.connect4game._about_game()
                            loading_msg = "Waiting for other player to enter their name"
                            loading_thread = Thread(target=self.simulate_loading_with_spinner, args=(loading_msg, self.unpickled_json, ))
                            self.unpickled_json_lock.release()
                            loading_thread.daemon = True
                            self.you = self.connect4game._get_player_name()
                            self.send_data({'you':self.you})
                            loading_thread.start()                        
                        elif "opponent" in self.unpickled_json:                                                             
                            self.opponent = self.unpickled_json['opponent']
                            self.unpickled_json_lock.release()
                            if not self.you:
                                self.connect4game._about_game()
                                self.you = self._get_other_player_name(self.opponent)
                                self.send_data({'you':self.you})                        
                            print("You are up against: ", self.opponent)                        
                            # Shuffling player names
                            if not self.ID:
                                first_player = self.connect4game._shuffle_players([self.you, self.opponent])
                                self.send_data({'first':first_player})                      
                            else:
                                print("Randomly choosing who to go first . . .")                
                        elif "first" in self.unpickled_json:
                            first = self.unpickled_json['first'][0]
                            loading_msg = f"Waiting for {self.opponent} to choose their color"
                            loading_thread = Thread(target=self.simulate_loading_with_spinner, args=(loading_msg, self.unpickled_json, ))
                            self.unpickled_json_lock.release()
                            loading_thread.daemon = True
                            if self.ID:
                                print(f"{first} goes first")
                            if first == self.you:
                                colors = self.connect4game._get_players_colors(self.you)
                                self.send_data({'colors':colors})
                            else:
                                loading_thread.start()                            
                        elif "colors" in self.unpickled_json:                                                                                     
                            colors = self.unpickled_json['colors']                        
                            self.unpickled_json_lock.release()
                            if first == self.you:
                                self.your_turn = True
                                self.player = Player(self.you, colored('O', colors[0], attrs=['bold']))                            
                            else:
                                self.your_turn = False
                                self.player = Player(self.you, colored('O', colors[1], attrs=['bold']))                        
                            self.send_data({'opponent_player_object':self.player})
                        elif "opponent_player_object" in self.unpickled_json:
                            self.opponent = self.unpickled_json['opponent_player_object']                        
                            self.unpickled_json_lock.release()
                            main_game_thread = Thread(target=self.main_game_thread)
                            main_game_thread.daemon = True
                            with self.condition:
                                main_game_thread.start()
                            self.main_game_started.set()
                        elif "board" in self.unpickled_json:
                            self.board = self.unpickled_json['board']
                            self.unpickled_json_lock.release()
                            self.board_updated_event.set() 
                            with self.condition:
                                self.condition.notify()
                        elif "round_over" in self.unpickled_json and "winner" in self.unpickled_json:
                            self.round_over_json = self.unpickled_json
                            self.unpickled_json_lock.release()
                            self.round_over_event.set()
                        elif 'play_again' in self.unpickled_json:
                            self.play_again_reply = self.unpickled_json['play_again']
                            self.unpickled_json_lock.release()
                            self.play_again_reply_received.set()                        
                            with self.condition:
                                self.condition.notify()
                        elif 'first_player' in self.unpickled_json:
                            self.first_player_for_next_round = self.unpickled_json['first_player']
                            self.unpickled_json_lock.release()
                            self.first_player_received.set()
                            with self.condition:
                                self.condition.notify()
                        elif 'timeout' in self.unpickled_json:
                            print(colored(self.unpickled_json['timeout'], "red", attrs=['bold']))
                            self.unpickled_json_lock.release()
                            receiving = False
                            break                
                    except socket.error:
                        if not self.keyboard_interrupt_event.is_set():
                            self._set_up_to_terminate_program(general_error_msg)
                        receiving = False
                        break                    
                    except Exception: # Catch EOFError and other exceptions
                        # NOTE: EOFError can also be raised when input() is interrupted with a Keyboard Interrupt
                        if not self.keyboard_interrupt_event.is_set():
                            self._set_up_to_terminate_program(something_went_wrong_msg)
                        receiving = False
                        break
                    # -------------------------------------Use unpickled json data here-------------------------------------
                    # Remove the processed message from the buffer
                    buffer = buffer[self.HEADERSIZE + message_length:]   
                else:
                    # Incomplete message, break out of the loop and wait for more data
                    break 

        self.stop_flag.set()
        if self.keyboard_interrupt_event.is_set():
            self.connect_again.clear()
        
        self.play_game_thread_complete.set()


if __name__ == "__main__":
    client = Client()
    try:
        while client.connect_again.is_set():
            zeroconf = Zeroconf()
            print("\nSearching for Connect4 Game service...\n\n")
            ServiceBrowser(zeroconf, service_type, client)
            start_time = time.time()

            # Loop until the service is found or the timeout is reached
            while not client.service_found:
                elapsed_time = time.time() - start_time
                if elapsed_time >= TIMEOUT_FOR_SERVICE_SEARCH:
                    break

                time.sleep(1)

            zeroconf.close()

            if client.service_found:
                client.service_found = False
                client.connect_to_game()
                while not client.stop_flag.is_set():  #  simulate work to keep main thread alive while other threads work
                    time.sleep(0.1)
                client.wait_for_threads()
            else:
                print(colored("Connect4 Service not found. Unable to start game. Make sure the server is running and you are connected to the server's local network", "red", attrs=['bold']))  
                client.connect_again.clear()
    except KeyboardInterrupt:
        client.terminate_program()