#! /usr/bin/env python3

# Everytime the contents of .fidl file is changed, this script should be run to create new passes and injectors
# It is assumed that the script is executed in the <llfisrc>/tools/FIDL/ directory

import sys, os
import shutil, errno
import subprocess
import yaml

################################################################################

script_path = os.path.realpath(os.path.dirname(__file__))

### PHIL @ July 12
llfiroot = os.path.dirname(os.path.dirname(script_path))
software_fault_injectors = os.path.join(llfiroot, 'runtime_lib/_SoftwareFaultInjectors.cpp')
software_failures_passes_dir = os.path.join(llfiroot, 'llvm_passes/software_failures/')
cmakelists = os.path.join(llfiroot, 'llvm_passes/CMakeLists.txt')
gui_software_fault_list = os.path.join(llfiroot, 'gui/config/customSoftwareFault_list.txt')
setup_script = os.path.join(llfiroot, 'setup.py')

################################################################################

#read .yaml file, and calculates the number of trigger points

def read_input_fidl(input_fidl):
  global F_Class, F_Mode, SpecInstsIndexes, numOfSpecInsts, Insts, custom_injector, action, reg_type, trigger_type

  # Check for Input FIDL's presence
  try:
    f = open(input_fidl, 'r')
  except:
    print('ERROR: Specified FIDL config file (%s) not found!' % input_fidl)
    exit(1)
  
  # Check for correct YAML formatting
  try:
    doc = yaml.load(f)
    f.close()
  except:
    print('Error: %s is not formatted in proper YAML format (reminder: use spaces, not tabs)' % input_fidl)
    exit(1)

  # Load values and check if FIDL options are valid
  
  try:
    F_Class = doc['Failure_Class']
    F_Mode = doc['Failure_Mode']
    nfm = doc['New_Failure_Mode']
    
    # parses 'Trigger'
    trigger = nfm['Trigger']
    
    if 'call' in trigger:
      Insts = trigger['call']
      trigger_type = 'call'
      
      # parses 'Target'
      target = nfm['Target']
      if 'src' in target and 'dst' in target:
        raise Exception('Error: Invalid trigger module (both src and dst usage not allowed)')
      elif 'src' in target:
        insts = target['src']
        if (set(insts) != set(Insts) or # need to specify at least one src for each instruction
            bool([inst for inst in insts.values() if inst == None or inst == [] or inst == '' or not isinstance(inst, list)])): # check that the specified src's aren't empty, an empty list, or an empty string, or isn't a list
          raise Exception("Error: Invalid number/name of src's in Target, or Target sources are not specified as list!")
        else:
          Insts = insts
        reg_type = 'src'
      elif 'dst' in target:
        reg_type = 'dst'
      elif 'RetVal' in target:
        reg_type = 'RetVal'
      else:
        raise Exception('Error: Invalid register target type!')
    elif 'RetVal' in trigger:
      Insts = trigger['RetVal']
      trigger_type = 'RetVal'
    else:
      raise Exception('Error: Trigger option (call, or ret) not found!')
      
    # parses 'Trigger*'
    if 'Trigger*' in nfm:
      trigger_s = nfm['Trigger*']
      SpecInstsIndexes = ', '.join(str(s) for s in trigger_s)
      numOfSpecInsts = len(trigger_s)
    else:
      SpecInstsIndexes = ''
      numOfSpecInsts = 0
      
    # parses 'Action'
    action = nfm['Action']
    if 'Perturb' in action:
      perturb = action['Perturb']
      if 'Custom_Injector' in perturb:
        custom_injector = doc['Custom_Injector']
      
  except Exception as e:
    raise e

################################################################################

def gen_ftrigger_single():
  # convert trigger and target of .fidl file into appropriate llvm passes
  # os.chdir("llfisrc/Templates/")
  MapLines = read_file('TargetDestinationTemplate.cpp')
  
  # print(MapLines)
  M1 = MapLines.index('//fidl_1')
  
  MapLines.insert(M1 + 1, 'class _%s_%sInstSelector : public SoftwareFIInstSelector {' % (F_Class, F_Mode))
  M2 = MapLines.index('//fidl_2')
  MapLines.insert(M2 + 1, '    _%s_%sInstSelector() {' % (F_Class, F_Mode))
  M3 = MapLines.index('//fidl_3')
  for i in Insts:
    MapLines.insert(M3 + 1, '        funcNames.insert(std::string("%s"));' % i) 
  M4 = MapLines.index('//fidl_4')
  MapLines.insert(M4 + 1, '        info["failure_class"] = "%s";' % (F_Class))
  MapLines.insert(M4 + 2, '        info["failure_mode"] = "%s";' % (F_Mode))
  MapLines.append('static RegisterFIInstSelector A("%s(%s)", new _%s_%sInstSelector());' % (F_Mode, F_Class, F_Class, F_Mode))
  
  # change reg_type
  if reg_type == 'src':
  	MapLines.append('static RegisterFIRegSelector B("%s(%s)", new FuncArgRegSelector(%s));\n\n}\n' % (F_Mode, F_Class, next(iter(Insts.values()))[0]))
  elif reg_type == 'dst':
  	MapLines.append('static RegisterFIRegSelector B("%s(%s)", new FuncDestRegSelector());\n\n}\n' % (F_Mode, F_Class))
  # @PHIL Bugfix July 22 # doesnt work still
  elif reg_type == 'RetVal': 
  	PassLines.append('static RegisterFIRegSelector B("%s(%s)", new RetValRegSelector());\n\n}\n' % (F_Mode, F_Class))

  AA = MapLines.index('//fidl_5')

  MapLines.insert(AA + 1, '                long numOfSpecInsts = %s;' % (numOfSpecInsts))
  MapLines.insert(AA + 2, '                long IndexOfSpecInsts[] = {%s};' % (SpecInstsIndexes))
  
  return MapLines
  
################################################################################
  
def gen_ftrigger_multisrc():
  PassLines = read_file('TargetSourceTemplate.cpp')
  
  A = PassLines.index('//fidl_1')
  PassLines.insert(A + 1, 'class _%s_%sInstSelector : public SoftwareFIInstSelector {' % (F_Class,F_Mode)) # Trigger: "fread"
  B = PassLines.index('//fidl_2')
  PassLines.insert(B + 1,'    _%s_%sInstSelector () {' % (F_Class,F_Mode))
  X = PassLines.index('//fidl_3')
           
  for inst in Insts:
    PassLines.insert(X + 1, '            funcNamesTargetArgs["%s"] = std::set<int>();' % inst)
    for reg in Insts[inst]:
      PassLines.insert(X + 2, '            funcNamesTargetArgs["%s"].insert(%s);' % (inst, reg))
     
  C = PassLines.index('//fidl_4')
  PassLines.insert(C + 1, '        info["failure_class"] = "%s";' % (F_Class))
  PassLines.insert(C + 2, '        info["failure_mode"] = "%s";' % (F_Mode))

  F = PassLines.index('    virtual bool isRegofInstFITarget(Value *reg, Instruction *inst) {')
  PassLines.insert(F + 5, '        if (_%s_%sInstSelector::isTarget(CI, reg)) {\n            return true;\n        }' % (F_Class,F_Mode))
  PassLines.append('static RegisterFIInstSelector A("%s(%s)", new _%s_%sInstSelector());' % (F_Mode, F_Class, F_Class, F_Mode))
  
  PassLines.append('static RegisterFIRegSelector B("%s(%s)", new _%s_%sRegSelector());\n\n}\n'%(F_Mode, F_Class, F_Class, F_Mode))
  # print(PassLines)
    
  AA = PassLines.index('//fidl_6')

  PassLines.insert(AA + 1, '            long numOfSpecInsts = %s;' % (numOfSpecInsts))
  PassLines.insert(AA + 2, '            long IndexOfSpecInsts[] = {%s};' % (SpecInstsIndexes))

  F = PassLines.index('//fidl_7')
  PassLines.insert(F + 1, 'std::map<std::string, std::set<int> >  _%s_%sInstSelector::funcNamesTargetArgs;\n' % (F_Class, F_Mode))
  PassLines.insert(F + 2, 'class _%s_%sRegSelector: public SoftwareFIRegSelector {' % (F_Class, F_Mode))  
  
  # @PHIL Bugfix July 21
  PassLines.insert(PassLines.index('//fidl_8') + 1, '        if (_%s_%sInstSelector::isTarget(CI, reg)) {\n            return true;' % (F_Class, F_Mode))
  
  return PassLines
  
################################################################################
  
def FTriggerGenerator() :
  # complete instrumenting pass development by printing the pass content into a file.
  # write to a file
  filename = '_%s_%sSelector.cpp' % (F_Class, F_Mode)
  filepath = os.path.join(software_failures_passes_dir, filename)
    
  if reg_type == 'src' and not is_one_src_register(): # multisrc
    write_file(filepath, gen_ftrigger_multisrc())
    print('Instrument module created.')
  elif reg_type == 'src' or reg_type == 'dst': # dst or singlesrc
    write_file(filepath, gen_ftrigger_single())
    print('Instrument module created.')
  else:
    print('Check your target format!')
  
  # modify llvm_pass/CMakeLists.txt
  l = read_file(cmakelists)
  
  try:
    l.index('  software_failures/%s' % filename) 
  except:
    l.insert(l.index('  #FIDL') + 1, '  software_failures/%s' % filename)
    write_file(cmakelists, l)

  # modify GUI's list
  l = read_file(gui_software_fault_list)
  
  try:
    l.index('%s(%s)' % (F_Mode, F_Class))
  except:
    l.append('%s(%s)' % (F_Mode, F_Class))
    write_file(gui_software_fault_list, l)
  
   # print (PassLines)
   
################################################################################
   
# checks if we are only instrumenting a single src register

def is_one_src_register():
  init_val = next(iter(Insts.values()))[0]
  for inst in Insts.values():
    if len(inst) > 1 or inst[0] != init_val:
      return False
  
  return True
      
################################################################################

def FInjectorGenerator():

  InjectorLines = read_file('Built-in-FITemplate.cpp')
  
  M = InjectorLines.index('static RegisterFaultInjector AN("DataCorruption(Data)", BitCorruptionInjector::getBitCorruptionInjector());')
  N = InjectorLines.index('static RegisterFaultInjector CD("NoAck(MPI)", new HangInjector());')
  O = InjectorLines.index('static RegisterFaultInjector DB("CPUHog(Res)", new SleepInjector());')
  P = InjectorLines.index('static RegisterFaultInjector BA("MemoryLeak(Res)", new MemoryLeakInjector());')
  S = InjectorLines.index('static RegisterFaultInjector EH("PacketStorm(MPI)", new ChangeValueInjector(-40, false));')
  T = InjectorLines.index('static RegisterFaultInjector FB("NoClose(API)", new InappropriateCloseInjector(false));')
  U = InjectorLines.index('static RegisterFaultInjector HB("LowMemory(Res)", new MemoryExhaustionInjector(false));')
  V = InjectorLines.index('static RegisterFaultInjector IB("WrongSavedFormat(I/O)", new WrongFormatInjector());')
  W = InjectorLines.index('static RegisterFaultInjector JA("DeadLock(Res)", new PthreadDeadLockInjector());')
  X = InjectorLines.index('static RegisterFaultInjector KA("ThreadKiller(Res)", new PthreadThreadKillerInjector());')
  Y = InjectorLines.index('static RegisterFaultInjector LA("RaceCondition(Timing)", new PthreadRaceConditionInjector());')
  
  # print(Type)     
  if 'Corrupt' in action:
    InjectorLines.insert(M + 1,'static RegisterFaultInjector AO("%s(%s)", BitCorruptionInjector::getBitCorruptionInjector());' % (F_Mode, F_Class))
    # print('i am in corrupt')
    # print("compilation successful")
  elif 'Freeze' in action:	
    InjectorLines.insert(N + 1,'static RegisterFaultInjector CE("%s(%s)", new HangInjector());' % (F_Mode, F_Class))
    # print('i am in freeze')
    # print("compilation successful")
  elif 'Delay' in action:	 
    InjectorLines.insert(O + 1,'static RegisterFaultInjector DC("%s(%s)", new SleepInjector());' % (F_Mode, F_Class))
    # print('i am in delay')
    # print("compilation successful")
  elif 'Perturb' in action:
    perturb = action['Perturb']
    if 'MemoryLeakInjector' in perturb:
      InjectorLines.insert(P + 1, 'static RegisterFaultInjector BB("%s(%s)", new MemoryLeakInjector());' % (F_Mode, F_Class))
      # print('i am in built-in perturb') 
      # print("compilation successful")
    elif 'ChangeValueInjector' in perturb:
      InjectorLines.insert(S + 1, 'static RegisterFaultInjector EI("%s(%s)", new ChangeValueInjector(-40, false));' % (F_Mode, F_Class))
      # print("compilation successful")
    elif 'InappropriateCloseInjector' in perturb:
      InjectorLines.insert(T + 1, 'static RegisterFaultInjector FC("%s(%s)", new InappropriateCloseInjector(false));' % (F_Mode, F_Class))
      # print("compilation successful")
    elif 'MemoryExhaustionInjector' in perturb:
      InjectorLines.insert(U + 1, 'static RegisterFaultInjector HC("%s(%s)", new MemoryExhaustionInjector(false));' %( F_Mode, F_Class))
      # print("compilation successful")
    elif 'WrongFormatInjector' in perturb:
      InjectorLines.insert(V + 1, 'static RegisterFaultInjector IC("%s(%s)", new WrongFormatInjector());' % (F_Mode, F_Class))
      # print("compilation successful")
    elif 'PthreadDeadLockInjector' in perturb:
      InjectorLines.insert(W + 1, 'static RegisterFaultInjector JB("%s(%s)", new PthreadDeadLockInjector());' % (F_Mode, F_Class))
      # print("compilation successful")
    elif 'PthreadThreadKillerInjector' in perturb:
      InjectorLines.insert(X + 1, 'static RegisterFaultInjector KB("%s(%s)", new PthreadThreadKillerInjector());' % (F_Mode, F_Class))
      # print("compilation successful")
    elif 'PthreadRaceConditionInjector' in perturb:
      InjectorLines.insert(Y + 1, 'static RegisterFaultInjector LB("%s(%s)", new PthreadRaceConditionInjector());' % (F_Mode, F_Class))
      # print("compilation successful")
    elif 'Custom_Injector' in perturb:
      InjectorLines.extend(gen_custom_injector())
    else:
      print('Error: Invalid Perturb Injector!')
      exit(1)
  else:
    print('Error: Invalid Action!')
    exit(1)
    
  write_file(software_fault_injectors, InjectorLines)
  
################################################################################

def gen_custom_injector():
  global custom_injector
  
  # format the custom injector lines
  custom_injector = '        ' + custom_injector                # add spaces before the first line
  custom_injector = custom_injector.rstrip('\n')                # remove last \n character
  custom_injector = custom_injector.replace('\n', '\n        ') # add spaces after every \n character
  
  # read template
  NInjectorLines = read_file('NewInjectorTemplate.cpp')
  
  # modify template
  NInjectorLines[0] = 'class %s_%sFInjector : public SoftwareFaultInjector {' % (F_Class, F_Mode)
  NInjectorLines[5] = custom_injector
  NInjectorLines.append('static RegisterFaultInjector X("%s(%s)", new %s_%sFInjector());' % (F_Mode, F_Class, F_Class, F_Mode)) 
  
  return NInjectorLines

################################################################################

def read_file(file_name):
  with open(file_name) as f:
    lines = f.read().splitlines()
  return lines
  
def write_file(file_name, lines):
  with open(file_name, 'w') as f:
    for line in lines:
      f.write('%s\n' % line)
      
################################################################################

def main(InputFIDL):
  read_input_fidl(InputFIDL)	 
  FTriggerGenerator()
  FInjectorGenerator()
  print ('Injector module created.')

################################################################################

if __name__ == '__main__':
  main(sys.argv[1])

