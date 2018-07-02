import pyaudio
import wave
import time
import sys
import os
import zmq
import serial
import struct
import RPi.GPIO as GPIO


# Classes and functions

class WavPlayer():
    def __init__(self, pin = 5):
        
        self.pin = pin
        self.pa = pyaudio.PyAudio()
        self.wf = None
        self.played = False
    
        # init the pins
        GPIO.setup(self.pin, GPIO.OUT)
        GPIO.output(self.pin, GPIO.LOW)
    
    
    def play_callback(self, in_data, frame_count, time_info, status):
        data = self.wf.readframes(frame_count)
        return (data, pyaudio.paContinue)
        
    
    def play_file(self, wave_file_path):
        self.wf = wave.open(wave_file_path, 'rb')
        stream = self.pa.open(format=self.pa.get_format_from_width(self.wf.getsampwidth()),
                channels=self.wf.getnchannels(),
                rate=self.wf.getframerate(),
                output=True,
                stream_callback=self.play_callback)
        
        GPIO.output(self.pin, GPIO.HIGH)
        stream.start_stream()
        
        while stream.is_active():
            #time.sleep(0.1)
            pass
        GPIO.output(self.pin, GPIO.LOW)
        time.sleep(0.1)
        stream.stop_stream()
        stream.close()
        self.flush_file()
    
    
    def flush_file(self):
        self.wf = None
        self.played = False
        
class SerialOutput():
    def __init__(self, port="/dev/ttyS0", baudrate=300):
        self.port = port
        self.baudrate = baudrate
        self.serial = serial.Serial(port=port, baudrate=self.baudrate)
    
    def open_out(self):
        self.serial.close()
        self.serial.open()
        if self.serial.isOpen():
            print "Serial is open!"
    
    def close(self):
        self.serial.close()
    
    def write_number(self, number, dtype='L'):
        self.serial.write(struct.pack(dtype, number))
    

# receives a line and turns it into a dictionary
# the line has one word for the command and n pairs that go to key, value (separator is space)
def parse_command(cmd_str):
    split_cmd = cmd_str.split(' ')
    assert(len(split_cmd)%2)
    cmd_par = {split_cmd[i] : split_cmd[i+1] for i in range(1, len(split_cmd), 2)}
    cmd = split_cmd[0]
    return cmd, cmd_par

def execute_command(cmd, pars):
    command = command_functions[cmd]
    response = command(pars)
    return response

def run_trial(trial_pars):
    #for now the trial is just playing a sound file
    # read the parameters
    wavefile_path = trial_pars['stim_file']
    trial_number = int(float(trial_pars['number']))
    
    # do the deed
    so.write_number(trial_number)
    time.sleep(0.5)
    wp.play_file(wavefile_path)
    return 'played'

def init_board():
    # init the board, the pins, and everything
    GPIO.setmode(GPIO.BCM)
    return 'ok'

def state_machine():
    command_functions = {'trial' : run_trial, 'init' : init_board}

    # Configuration of Pins
    pin_audio = 26 
    port = "5558"
    wave_file = os.path.abspath('/root/experiment/stim/audiocheck.net_sin_1000Hz_-3dBFS_3s.wav')

    # a very simple server that waits for commands
    context = zmq.Context()
    socket = context.socket(zmq.REP)
    socket.bind("tcp://*:%s" % port)
    print('Setup ZMQ')

    while True:
        print('Waiting for commands...')
        # Wait for next request from client
        command = socket.recv()
        print("Received request: " + command)
        
        cmd, cmd_par = parse_command(command)
        response = execute_command(cmd, cmd_par)
        time.sleep(1) 
        socket.send("%s from %s" % (response, port))

command_functions = {'trial' : run_trial, 'init' : init_board}

if __name__ == '__main__':
    print('Gentnerlab OpenEphys Rig State Machine')
    print('Originally by Zeke Arneodo, Modified by Brad Theilman')
    # start the wave player
    init_board()
    wp = WavPlayer()
    so = SerialOutput()
    state_machine()
