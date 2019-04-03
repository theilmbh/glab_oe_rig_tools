#!/usr/bin/env python
from tkinter import *
import os
import threading
import sys
import socket
from time import sleep
import zmq
import time
import logging
import glob
import wave
import datetime
import numpy as np
import scipy.io.wavfile as wavfile
from PIL import Image, ImageTk
from paramiko import SSHClient
from scp import SCPClient
from serial_commander import conex_interface as sc

#################################
## ACUTE RIG CONTROL GUI!      ##
## Brad Theilman 2018          ##
## With code from Zeke Arneodo ##
#################################


def parse_command(cmd_str):
    """
    # the line has one word for the command and n pairs that go to key, value (separator is space)
    :param cmd_str: string with name of command and pairs of params and values
    :return: cmd : str (name of the command)
            cmd_par: dictionary {par_name: str(par_value)} with the parameters for the command
    """
    split_cmd = cmd_str.split(' ')
    assert (len(split_cmd) % 2)
    cmd_par = {split_cmd[i]: split_cmd[i + 1] for i in range(1, len(split_cmd), 2)}
    cmd = split_cmd[0]
    return cmd, cmd_par


class OpenEphysEvents:

    def __init__(self, port='5556', ip='127.0.0.1'):
        self.ip = ip
        self.port = port
        self.socket = None
        self.context = None
        self.timeout = 5.
        self.last_cmd = None
        self.last_rcv = None

    def connect(self):
        url = "tcp://%s:%d" % (self.ip, int(self.port))
        self.context = zmq.Context()
        self.socket = self.context.socket(zmq.REQ)
        self.socket.RCVTIMEO = int(self.timeout * 1000)
        self.socket.connect(url)

    def start_acq(self, ):
        if self.query_status('Acquiring'):
            print('Already acquiring')
        else:
            self.send_command('StartAcquisition')
            if self.query_status('Acquiring'):
                print('Acquisition Started')
            else:
                print('Something went wrong starting acquisition')

    def stop_acq(self, ):
        if self.query_status('Recording'):
            print('Cant stop acquistion while recording')

        elif not self.query_status('Acquiring'):
            print('No acquisition running')

        else:
            self.send_command('StopAcquisition')
            if not self.query_status('Acquiring'):
                print('Acquistion stopped')
            else:
                print('Something went wrong stopping acquisition')

    def start_rec(self, rec_par={'CreateNewDir': '0',
                                 'RecDir': None,
                                 'PrependText': None,
                                 'AppendText': None}):
        ok_to_start = False
        ok_started = False

        if self.query_status('Recording'):
            print('Already Recording')

        elif not self.query_status('Acquiring'):
            print('Was not Acquiring')
            self.start_acq()
            if self.query_status('Acquiring'):
                ok_to_start = True
                print('OK to start')
        else:
            ok_to_start = True
            print('OK to start')

        if ok_to_start:
            rec_opt = ['{0}={1}'.format(key, value)
                       for key, value in rec_par.items()
                       if value is not None]
            self.send_command(' '.join(['StartRecord'] + rec_opt))
            if self.query_status('Recording'):
                print('Recording path: {}'.format(self.get_rec_path()))
                ok_started = True
            else:
                print('Something went wrong starting recording')
        else:
            print('Did not start recording')
        return ok_started

    def stop_rec(self):
        if self.query_status('Recording'):
            self.send_command('StopRecord')
            if not self.query_status('Recording'):
                print('Recording stopped')
            else:
                print('Something went wrong stopping recording')
        else:
            print('Was not recording')

    def break_rec(self):
        ok_to_start = False
        ok_started = False
        print('Breaking recording in progress')
        if self.query_status('Recording'):
            self.send_command('StopRecord')
            if not self.query_status('Recording'):
                #print('Recording stopped')
                ok_to_start = True
                #print('OK to start')
            else:
                print('Something went wrong stopping recording')

        else:
            print('Was not recording')

        if ok_to_start:
            #print('trying to record')
            self.send_command('StartRecord')
            if self.query_status('Recording'):
                #print('Recording path: {}'.format(self.get_rec_path()))
                ok_started = True
            else:
                print('Something went wrong starting recording')

    def get_rec_path(self):
        return self.send_command('GetRecordingPath')

    def query_status(self, status_query='Recording'):
        query_dict = {'Recording': 'isRecording',
                      'Acquiring': 'isAcquiring'}

        status_queried = self.send_command(query_dict[status_query])
        return True if status_queried == b'1' else False if status_queried == b'0' else None

    def send_command(self, cmd):
        self.socket.send_string(cmd)
        self.last_cmd = cmd
        self.last_rcv = self.socket.recv()
        return self.last_rcv

    def close(self):
        self.stop_rec()
        self.stop_acq()
        self.context.destroy()

class RigStateMachineConnection:

    def __init__(self, port='5558', ip='192.168.1.5', timeout_s=90.):
        self.ip = ip
        self.port = port
        self.socket = None
        self.context = None
        self.timeout = int(timeout_s * 1000) # timeout in ms
        self.last_cmd = None
        self.last_rcv = None

    def connect(self):
        url = "tcp://%s:%d" % (self.ip, int(self.port))
        self.context = zmq.Context()
        self.socket = self.context.socket(zmq.REQ)
        self.socket.RCVTIMEO = self.timeout
        self.socket.SNDTIMEO = self.timeout
        self.socket.connect(url)

    def send_command(self, cmd):
        self.socket.send_string(cmd)
        self.last_cmd = cmd
        # stays locked until commands executes and response comes back
        # this should go on a thread of the program that uses it
        self.last_rcv = self.socket.recv()
        return self.last_rcv

    def close(self):
        self.context.destroy()

    def start_trial(self, stimulus_path, number):
        cmd = 'trial ' + 'stim_file {} '.format(stimulus_path) + 'number {}'.format(number)
        print('Sending: {}'.format(cmd))
        self.send_command(cmd)

class CONEXControl:
    def __init__(self, acuterig):
        os.system("xset r off") # Turn off keyboard repeat
        self.master = acuterig.conex_window
        self.master.protocol("WM_DELETE_WINDOW", self.on_closing)
        self.zcoord = 0
        self.homepos = 0
        self.posString = StringVar()
        self.posString.set(str(self.zcoord))
        self.initialize_window()
        self.con = sc.SerialCommander() # Our connection to the drive
        self.con.reference()
        self.initial_drive_position = self.con.getCurrPosition()

    def on_closing(self):
        os.system("xset r on")
        self.con.close()
        self.master.destroy()

    def initialize_window(self):

       self.master.title(string="CONEX Control")
       self.master.bind("<Key>", self.process_key)
       self.master.aspect(1, 1, 1, 1)
       self.buttonframe = Frame(self.master, bd=2)
       self.down5 = Button(self.buttonframe, text="Down 5um",command=lambda : self.move_stage(5))
       self.down25 = Button(self.buttonframe, text="Down 25um",command=lambda : self.move_stage(25))
       self.down50 = Button(self.buttonframe, text="Down 50um",command=lambda : self.move_stage(50))
       self.up25 = Button(self.buttonframe, text="Up 25um",command=lambda : self.move_stage(-25))
       self.up50 = Button(self.buttonframe, text="Up 50um",command=lambda : self.move_stage(-50))
       self.up100 = Button(self.buttonframe, text="Up 100um",command=lambda : self.move_stage(-100))

       self.gohome = Button(self.buttonframe, text="Go Home", command=self.rethome)

       self.up25.grid(row=0, column=0)
       self.up50.grid(row=0, column=1)
       self.up100.grid(row=0, column=2)
       self.gohome.grid(row=1, column=1)
       self.down5.grid(row=2, column=0)
       self.down25.grid(row=2, column=1)
       self.down50.grid(row=2, column=2)

       self.buttonframe.grid(row=0, column=0, columnspan=3, rowspan=3)

       self.posdisplay = Frame(self.master, bd=2)

       self.positionlabel = Label(self.posdisplay, text="Current Z Position (um)", anchor=CENTER)
       self.positionentry = Entry(self.posdisplay, text = self.posString, bg="black", fg="#00ff33", font=(20), justify="center")
       self.positionlabel.pack()
       self.positionentry.pack()

       self.posdisplay.grid(row=3, column=0, columnspan=3, rowspan=2, sticky=N+S)

        
    def move_stage(self, dist, event=None):
        self.con.moveStage(dist)
        sleep(0.0025*abs(dist)+0.1)
        self.send_motion_event(dist)
        self.update_position_display()

    def update_position_display(self):
        pos = self.con.getCurrPosition()
        self.zcoord = pos - self.initial_drive_position
        self.posString.set(str("{:.1f}".format(self.zcoord)))

    def send_motion_event(self, dist):
        pass

    def rethome(self):
        self.con.goHome(self.initial_drive_position)
        sleep(0.1 + 0.0005*abs(self.zcoord - self.initial_drive_position)) 
        self.update_position_display()
    
    def process_key(self, event):
        keybindings = {'KP_7': -25, 'KP_8': -50, 'KP_9': -100, 'KP_1': 5, 'KP_2': 25, 'KP_3': 50}
        if event.keysym == 'KP_5':
            self.rethome()
        elif event.keysym in keybindings.keys():
            self.move_stage(keybindings[event.keysym])

class AcuteExperimentControl:

    def __init__(self, master):
        self.master_window = master
        self.master_window.title('Gentnerlab Acute Rig Control')
        self.bird = 'default'
        
        # Probe information
        self.AP = 0
        self.ML = 0
        self.Z = 0
        self.probe = 'A1x16'

        # Stimulu information
        self.stim_dir = os.path.expanduser('~/stimuli/')

        # Trial information
        self.inter_trial_type = 'random'
        self.inter_trial_max = 5.0
        self.inter_trial_min = 2.0
        self.inter_trial_fixed = 5.0
        self.n_repeats = 1

        # Command Protocol
        self.rpi_port = 5556
        self.oe_port = 5558

        self.run_block_flag = None
        self.blocknum = 0
        self.search_or_block = "block"
        self.repeat_stim = False
        self.setup_gui()

    def setup_gui(self):
        # Bird / Probe / Location
        self.phys_params_frame = LabelFrame(self.master_window, text="Physical Parameters", padx=5)
        self.bird_label = Label(self.phys_params_frame, text="Bird")
        self.probe_label = Label(self.phys_params_frame, text="Probe")
        self.ap_label = Label(self.phys_params_frame, text="AP (um)")
        self.ml_label = Label(self.phys_params_frame, text="ML (um)")
        self.z_label = Label(self.phys_params_frame, text="Z (um)")

        self.bird_entry = Entry(self.phys_params_frame,width=8, justify='right')
        self.probe_entry = Entry(self.phys_params_frame, width=8, justify='right')
        self.ap_entry = Entry(self.phys_params_frame, width=8, justify='right')
        self.ml_entry = Entry(self.phys_params_frame, width=8, justify='right')
        self.z_entry = Entry(self.phys_params_frame, width=8, justify='right')

        self.bird_label.grid(row=0, column=0)
        self.probe_label.grid(row=1, column=0)
        self.ap_label.grid(row=2, column=0)
        self.ml_label.grid(row=3, column=0)
        self.z_label.grid(row=4, column=0)

        self.bird_entry.grid(row=0, column=1, sticky=W+E)
        self.probe_entry.grid(row=1, column=1)
        self.ap_entry.grid(row=2, column=1)
        self.ml_entry.grid(row=3, column=1)
        self.z_entry.grid(row=4, column=1)

        self.phys_params_frame.grid(row=0, column=0, rowspan=2, columnspan=4, padx=5, pady=5, sticky=W+E+N+S)

        self.bird_entry.insert(0, self.bird)
        self.probe_entry.insert(0, str(self.probe))
        self.ap_entry.insert(0, str(self.AP))
        self.ml_entry.insert(0, str(self.ML))
        self.z_entry.insert(0, str(self.Z))
        

        # Block Control
        self.block_labelframe = LabelFrame(self.master_window, text="Block Parameters", padx=5)
        self.iti_label = Label(self.block_labelframe, text="ITI Type", justify='center')
        self.block_type_label = Label(self.block_labelframe, text="Block Type", justify="center")
        self.itv = StringVar()
        self.itv.set("random")
        self.random_iti_button = Radiobutton(self.block_labelframe, text="Random", variable=self.itv, value="random", command=self.set_random_iti)
        self.fixed_iti_button = Radiobutton(self.block_labelframe, text="Fixed", variable=self.itv, value="fixed", command=self.set_fixed_iti)
        self.sob = StringVar()
        self.sob.set("block")
        self.search_button = Radiobutton(self.block_labelframe, text="Search", variable=self.sob, value="search", command=self.set_search)
        self.block_button = Radiobutton(self.block_labelframe, text="Block", variable=self.sob, value="block", command=self.set_block)

        self.iti_range_label = Label(self.block_labelframe, text="ITI Min (s)")
        self.iti_range_min_entry = Entry(self.block_labelframe, width=4, justify='right')
        self.iti_range_label_max = Label(self.block_labelframe, text="ITI Max (s)")
        self.iti_range_max_entry = Entry(self.block_labelframe, width=4, justify='right')
        
        self.n_repeats_label = Label(self.block_labelframe, text='Repeats')
        self.n_repeats_entry = Entry(self.block_labelframe, width=4, justify='right')
        self.repeat_stimulus_button = Button(self.block_labelframe, text='Repeat Stimulus', command=self.flip_repeat_stimulus)

        self.iti_label.grid(row=0, column=0, columnspan=2)
        self.random_iti_button.grid(row=1, column=0) 
        self.fixed_iti_button.grid(row=1, column=1)
        self.block_type_label.grid(row=2, column=0, columnspan=2)
        self.search_button.grid(row=3, column=0)
        self.block_button.grid(row=3, column=1)
        self.iti_range_label.grid(row=4, column=0)
        self.iti_range_min_entry.grid(row=4, column=1)
        self.iti_range_label_max.grid(row=5, column=0)
        self.iti_range_max_entry.grid(row=5, column=1)
        
        self.n_repeats_label.grid(row=6, column=0)
        self.n_repeats_entry.grid(row=6, column=1)
        self.repeat_stimulus_button.grid(row=7, column=0, columnspan=2)

        self.block_labelframe.grid(row=2, column=0, rowspan=3,columnspan=4, padx=5, sticky=W+E+N+S)

        self.n_repeats_entry.insert(0, str(self.n_repeats))
        self.iti_range_min_entry.insert(0, str(self.inter_trial_min))
        self.iti_range_max_entry.insert(0, str(self.inter_trial_max))
        

        # Stimulus Path
        self.paths_frame = LabelFrame(self.master_window, bd=2, text="Path Parameters")
        self.load_stimulus_button = Button(self.paths_frame, text='Load Stimuli', command=self.load_stimuli)
        self.experiment_path_label = Label(self.paths_frame, text='Experiment Dir')
        self.experiment_path_entry = Entry(self.paths_frame)
        self.stimulus_path_label = Label(self.paths_frame, text='Stimulus Dir')
        self.stimulus_path_entry = Entry(self.paths_frame)
        self.session_label = Label(self.paths_frame, text='Session ID')
        self.session_entry = Entry(self.paths_frame)

        self.stimulus_path_label.grid(row=1, column=0)
        self.stimulus_path_entry.grid(row=1, column=1, padx=5)
        self.experiment_path_label.grid(row=0, column=0)
        self.experiment_path_entry.grid(row=0, column=1, padx=5)
        self.session_label.grid(row=2, column=0)
        self.session_entry.grid(row=2, column=1, padx=5)

        self.stimulus_path_entry.insert(0, os.path.expanduser('~/stimuli'))
        self.experiment_path_entry.insert(0, os.path.expanduser('~/experiments/'))

        self.paths_frame.grid(row=2, column=4,  rowspan=1, columnspan=4, sticky=W+E+N+S, padx=5)

        # Block Start/Stop
        self.control_button_frame = Frame(self.master_window)
        self.stop_button = Button(self.control_button_frame, text='Stop', command=self.stop_button_cmd)
        self.start_button = Button(self.control_button_frame, text='Start', command=self.start_button_cmd)
        self.stop_button.grid(row=0, column=1, sticky='E')
        self.start_button.grid(row=0, column=2, sticky='E', padx=5)
        Button(self.control_button_frame, text='Setup Session', command=self.setup_session).grid(row=0, column=0)
        Button(self.control_button_frame, text='Open Conex Control', command=self.open_conex).grid(row=1, column=0)
        self.control_button_frame.grid(row=4, column=4, columnspan=4, sticky=S)

        # Block Status
        self.block_status_frame = LabelFrame(self.master_window, bd=2, relief='ridge', text="Block Status")
        self.block_min_label = Label(self.block_status_frame, text="Block Min: %.1f (s)" % 0)
        self.block_max_label = Label(self.block_status_frame, text = "Block Max: %.1f (s)" % 0)
        self.stimulus_status_label = Label(self.block_status_frame, text='No Stimuli')
        self.stimulus_status_label.grid(row=1, column=0, columnspan = 4, sticky='W')
        self.block_min_label.grid(row=0, column=0, columnspan=1)
        self.block_max_label.grid(row=0, column=1, columnspan=1)

        self.block_status_frame.grid(row=3, column=4, columnspan=4, rowspan=1, padx=5, pady=5, sticky=N+S+E+W)

        # Logo
        image = Image.open("glab.png").resize(size=(256, 64), resample=Image.BICUBIC)
        self.logo = ImageTk.PhotoImage(image)
        self.logo_label = Label(image=self.logo)
        self.logo_label.grid(row=0, column=4, columnspan=4, rowspan=2, pady=15, padx=10, sticky=N+S)

        # Author
        #Label(self.master_window, text="Brad Theilman").grid(row=11, column=4 )

        # Setup Session button

    def open_conex(self):
        self.conex_window = Toplevel(self.master_window)
        self.conex_app = CONEXControl(self)

    def start_button_cmd(self):
        self.lock_params()
        # Record all the current values
        self.bird = self.bird_entry.get()
        self.probe = self.probe_entry.get()
        self.AP = float(self.ap_entry.get())
        self.ML = float(self.ml_entry.get())
        self.Z = float(self.z_entry.get())
        self.n_repeats = int(self.n_repeats_entry.get())
        if self.inter_trial_type == 'random':
            self.inter_trial_max = float(self.iti_range_max_entry.get())
            self.inter_trial_min = float(self.iti_range_min_entry.get())
        else:
            self.inter_trial_fixed = float(self.iti_range_min_entry.get())
        self.stim_dir = self.stimulus_path_entry.get()
        print('Bird: {} Probe: {} AP: {} ML: {} Z:{}'.format(self.bird, self.probe, self.AP, self.ML, self.Z))
        self.start_block()

    def stop_button_cmd(self):
        if self.run_block_flag:
            self.run_block_flag.clear()
        self.unlock_params()
    
    def flip_repeat_stimulus(self):
        self.repeat_stim = not self.repeat_stim
        if self.repeat_stim:
            self.repeat_stimulus_button.config(text="Random Stim")
        else:
            self.repeat_stimulus_button.config(text="Repeat Stim")


    def lock_params(self):
        self.bird_entry.config(state=DISABLED)
        self.probe_entry.config(state=DISABLED)
        self.ap_entry.config(state=DISABLED)
        self.ml_entry.config(state=DISABLED)
        self.z_entry.config(state=DISABLED)
        self.n_repeats_entry.config(state=DISABLED)
        self.iti_range_max_entry.config(state=DISABLED)
        self.iti_range_min_entry.config(state=DISABLED)

    def unlock_params(self):
        self.bird_entry.config(state=NORMAL)
        self.probe_entry.config(state=NORMAL)
        self.ap_entry.config(state=NORMAL)
        self.ml_entry.config(state=NORMAL)
        self.z_entry.config(state=NORMAL)
        self.n_repeats_entry.config(state=NORMAL)
        self.iti_range_max_entry.config(state=NORMAL)
        self.iti_range_min_entry.config(state=NORMAL)

    def set_random_iti(self):
        self.inter_trial_type='random'
        self.iti_range_max_entry.config(state=NORMAL)
        self.iti_range_label.config(text='ITI Min (s)')

    def set_fixed_iti(self):
        self.inter_trial_type='fixed'
        self.iti_range_label.config(text='ITI Fixed (s)')
        self.iti_range_max_entry.config(state=DISABLED)

    def set_search(self):
        self.search_or_block = "search"

    def set_block(self):
        self.search_or_block = "block"

    def start_block(self):
        # Connect to Raspberry pi
        self.rpi = RigStateMachineConnection()
        self.rpi.connect()

        # Connect to OpenEphys
        self.openephys = OpenEphysEvents()
        self.openephys.connect()

        # Load Stimuli
        self.stimuli=['./test.wav', './test.wav', './test.wav']
        self.load_stimuli(self.stim_dir)

        # Add Sines to Stimuli
        self.add_sines_to_stimuli()

        # Compute Length
        (block_min, block_max) = self.compute_block_length()
        self.block_min_label.config(text="Block Min: %.1f (s)" % block_min)
        self.block_max_label.config(text="Block Max: %.1f (s)" % block_max)

        # Copy Stimuli
        self.copy_stimuli()
        print('Copied stimuli.')

        # prepare the block
        self.blocknum += 1
        self.setup_block_name(self.search_or_block)
        if self.search_or_block == "block":
            self.block_thread = threading.Thread(target=self.block_thread_task)
        else:
            self.block_thread = threading.Thread(target=self.search_thread_task)
        self.run_block_flag = threading.Event()
        self.run_block_flag.set()

        # Start Recording and Run the block
        self.openephys.start_acq()
        rec_params = {'CreateNewDir': '0', 'RecDir': self.block_path, 'PrependText': None, 'AppendText': None}
        self.openephys.start_rec(rec_params)
        time.sleep(5.0)
        self.block_thread.start()

    def block_thread_task(self):
        n_stims = len(self.stimuli)
        stim_order = np.tile(np.arange(n_stims), self.n_repeats)
        np.random.shuffle(stim_order)
        #print(stim_order)
        for trial_num, stim_num in enumerate(stim_order):
            # check to see if we need to stop
            if not self.run_block_flag.is_set():
                break

            if self.inter_trial_type == 'random':
                iti = (self.inter_trial_max - self.inter_trial_min)*np.random.random() + self.inter_trial_min
            else:
                iti = self.inter_trial_fixed
            stimulus_file = self.stimuli[stim_num]
            _, stimulus_name = os.path.split(stimulus_file)
            pi_stimulus_path = os.path.join('/home/pi/stimuli/', stimulus_name)
            print('Trial: {} Stimulus: {}'.format(trial_num, stimulus_file))
            # set stimulus status label
            self.stimulus_status_label.config(text="Stimulus: {}   {} of {}".format(stimulus_name, trial_num+1, len(stim_order)))
            # Send Stimulus Name to OpenEphys
            self.openephys.send_command('stim ' + stimulus_file)
            # Tell RPi to run trial
            self.rpi.start_trial(pi_stimulus_path, trial_num)
            print('ITI: {} seconds'.format(iti))
            time.sleep(iti)

        # clean up end of block
        self.openephys.close()
        self.unlock_params()
        self.stimulus_status_label.config(text="Block Finished")

    def search_thread_task(self):
        n_stims = len(self.stimuli)
        stimulus_file = self.stimuli[0]
        trial_num = 0
        while self.run_block_flag.is_set():
            trial_num += 1
         # is repeat stimulus set?  if not, choose a new stimulus to play
            if not self.repeat_stim:
                stimulus_file = self.stimuli[np.random.randint(n_stims)]

            if self.inter_trial_type == 'random':
                iti = (self.inter_trial_max - self.inter_trial_min)*np.random.random() + self.inter_trial_min
            else:
                iti = self.inter_trial_fixed
            _, stimulus_name = os.path.split(stimulus_file)
            pi_stimulus_path = os.path.join('/home/pi/stimuli/', stimulus_name)
            print('Search Trial: {} Stimulus: {}'.format(trial_num, stimulus_file))
            # set stimulus status label
            self.stimulus_status_label.config(text="Stimulus: {}".format(stimulus_name))
            # Send Stimulus Name to OpenEphys
            self.openephys.send_command('stim ' + stimulus_file)
            self.rpi.start_trial(pi_stimulus_path, trial_num)
            print('ITI: {} seconds'.format(iti))
            time.sleep(iti)

        # clean up end of block
        self.openephys.close()
        self.unlock_params()
        self.stimulus_status_label.config(text="Search Finished")

    def load_stimuli(self, path):
        wavfs = glob.glob(os.path.join(path, '*.wav'))
        self.stimuli = wavfs
    
    def add_sines_to_stimuli(self):
        if self.stimuli:
            self.sined_stim_names = []
            for stim in self.stimuli:
                fs, stim_dat = wavfile.read(stim)
                nsamps = len(stim_dat)
                t = np.arange(nsamps)
                sine_dat = (16384*np.sin(2*np.pi*(1000./fs)*t)).astype('int16')
                output_fname = stim + '.sine'
                output_data = np.zeros((nsamps, 2))
                output_data[:, 0] = sine_dat
                output_data[:, 1] = stim_dat
                wavfile.write(output_fname, fs, output_data.astype('int16')) 
                self.sined_stim_names.append(output_fname)
                print(stim_dat.dtype, sine_dat.dtype, output_data.dtype)
            self.unsined_stims = self.stimuli
            self.stimuli = self.sined_stim_names 

    def compute_block_length(self):
        if self.stimuli:
            durs = []
            for stim in self.stimuli:
                f= wave.open(stim, 'r')
                rate = f.getframerate()
                frames = f.getnframes()
                durs.append(frames / float(rate))
                f.close()
            durs = np.array(durs)*self.n_repeats
            stimdur = np.sum(durs)
            min_dur = stimdur + self.n_repeats*self.inter_trial_min*len(self.stimuli)
            max_dur = stimdur + self.n_repeats*self.inter_trial_max*len(self.stimuli)
            if self.inter_trial_type == 'fixed':
                min_dur = stimdur + self.n_repeats*self.inter_trial_fixed*len(self.stimuli)
                max_dur = min_dur
        return (min_dur, max_dur)
               
    def setup_block_name(self, search_or_block):
        #Format: Date-Time-Bird-Blocknum-AP-ML-Z
        self.block_name = datetime.datetime.now().strftime('%Y%m%d%H%M') + '-' + self.bird + '-' + '{}-{}-'.format(search_or_block, self.blocknum) + \
                'AP-%.0f-' % self.AP + 'ML-%.0f-' % self.ML + 'Z-%.0f' % self.Z
                
        self.block_path = os.path.join(self.blocks_path, self.block_name)
        os.makedirs(self.block_path, exist_ok=False)
        self.save_block_parameters(self.block_path)

    def save_block_parameters(self, path):
        pass

    def setup_session(self):
        
        self.sessionID = datetime.datetime.now().strftime('%Y%m%d') + '-' +socket.gethostname()
        self.session_path=os.path.join(self.experiment_path_entry.get(), self.sessionID)
        self.bird_path = os.path.join(self.session_path, self.bird)
        self.blocks_path = os.path.join(self.bird_path, 'blocks')
        os.makedirs(self.blocks_path, exist_ok=True)

        self.session_entry.delete(0, END)
        self.session_entry.insert(0, self.sessionID)

    def copy_stimuli(self):
        # Copies stimuli over to raspi via ssh
        ssh = SSHClient()
        ssh.load_system_host_keys()
        ssh.connect('192.168.1.5', username='pi')
        with SCPClient(ssh.get_transport()) as scp:
            for stimulus in self.stimuli:
                scp.put(stimulus, remote_path='/home/pi/stimuli')

    def run(self):
        self.master_window.mainloop()

if __name__ == '__main__':
    root = Tk()
    app = AcuteExperimentControl(root)
    app.run()
