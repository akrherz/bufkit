#!/usr/bin/perl
#===============================================================================
#
#         FILE:  Acquire.pm
#
#  DESCRIPTION:  Contains basic acquisition routines for bufrgruven
#                At least that's the plan
#
#       AUTHOR:  Robert Rozumalski - NWS
#      VERSION:  11.0
#      CREATED:  06/28/2011 10:31:20 PM
#     REVISION:  ---
#===============================================================================
#
package Acquire;
require 5.8.0;
use strict;
use warnings;
use English;
use vars qw (%Bgruven $mesg);

sub bufr {
#----------------------------------------------------------------------------------
#   Calls the various initialization routines and returns the %Bgruven hash
#----------------------------------------------------------------------------------
#
#  Bring in the Bgruven hash from main
#
%Bgruven = %main::Bgruven;

    &stations    or &Love::died($mesg); #  Reconcile requested stations with those in station list

    &acquire     or &Love::died($mesg); #  Initialize directories and other stuff

return %Bgruven;
}


sub stations {
#----------------------------------------------------------------------------------
#  Complete the initial settings before attempting to acquire BUFR files
#----------------------------------------------------------------------------------
#
    my $dset  = $Bgruven{BINFO}->{DSET}{dset};
    my $ymd   = $Bgruven{PROCESS}->{DATE}{yyyymmdd}; chomp $ymd;
    my $cc    = $Bgruven{PROCESS}->{DATE}{acycle};

    #  Populate the date, time, and model placeholders in LOCFIL
    #
    $Bgruven{BINFO}->{DSET}{locfil} = &Utils::fillit($Bgruven{BINFO}->{DSET}{locfil},$ymd,$cc,$dset,'MOD');

    #  Delete any files in the local working directory that are greater than 2 days old.
    #
    &Utils::mkdir($Bgruven{GRUVEN}->{DIRS}{bufdir});
    opendir DIR => $Bgruven{GRUVEN}->{DIRS}{bufdir};
    foreach (readdir(DIR)) {next if /^\./; &Utils::rm("$Bgruven{GRUVEN}->{DIRS}{bufdir}/$_") if -M "$Bgruven{GRUVEN}->{DIRS}{bufdir}/$_" > 2;}
    closedir DIR;

    &Utils::modprint(0,2,96,1,1,sprintf("%5s  Determining which BUFR files need to be acquired",shift @{$Bgruven{GRUVEN}->{INFO}{rn}}));

    if (@{$Bgruven{PROCESS}->{STATIONS}{invld}}) {
        &Utils::modprint(6,9,104,1,0,sprintf("Hey, station %-5s is not in station list - $Bgruven{BINFO}->{DSET}{stntbl}",$_)) foreach @{$Bgruven{PROCESS}->{STATIONS}{invld}};
        &Utils::modprint(0,9,104,1,1,' ');
    }

    #  Return if all the requested stations are invalid
    #
    unless (%{$Bgruven{PROCESS}->{STATIONS}{valid}}) {$mesg = "There are no valid stations in your list!"; return;}

    #  Get the list of BUFR files to download and process
    #
    #  For SREF stations it is possible that a subset of the member BUFR files will be
    #  available when BUFRgruven is run.  Should a new SREF member become available 
    #  following the acquisition and processing of a SREF station then ALL available
    #  members must be processed again. Otherwise, only those new members  will be 
    #  included in the GEMPAK and BUFKIT files.
    #
    #  It is assumed that the user wants BUFKIT data to reflect all currently available
    #  SREF members for a station. Thus, the default behaviour is to process ALL 
    #  available members whenever a new member becomes available. If the user wishes 
    #  to suspend processing until ALL members become available then see the comments
    #  at the bottom of the acquire subroutine.
    #
    %{$Bgruven{PROCESS}->{STATIONS}{process}} = ();
    %{$Bgruven{PROCESS}->{STATIONS}{acquire}} = ();

    #  Make an initial loop through all the requested stations and model/members. Create 
    #  a list that will be check against to determine whether ALL the files for a station
    #  and data set have been downloaded and processed previously. 
    #  
    my %n2p=();
    foreach my $stnm (sort { $a <=> $b } keys %{$Bgruven{PROCESS}->{STATIONS}{valid}}) {
        $n2p{$stnm}=0;
        foreach my $mod (@{$Bgruven{BINFO}->{DSET}{members}{order}} ? @{$Bgruven{BINFO}->{DSET}{members}{order}} : @{$Bgruven{BINFO}->{DSET}{model}}) {
            my $locfil;
            for ($locfil = $Bgruven{BINFO}->{DSET}{locfil}) {s/STNM/$stnm/g; s/MOD|MEMBER/$mod/g; $_="$Bgruven{GRUVEN}->{DIRS}{bufdir}/$locfil";}
            $n2p{$stnm}=1 unless -s $locfil;
        }
    }

    #  Note that from the previous block, if $n2p{$stnm}=0 then all BUFR files for station 
    #  and data set were downloaded and processed previously. If a single member or BUFR
    #  file for a station/data set is missing then $n2p{$stnm}=1 and ALL the previously 
    #  downloaded and processed BUFR files for that station/data set will be scheduled
    #  for processing provided that any missing BUFR files are acquired.
    #
    foreach my $mod (@{$Bgruven{BINFO}->{DSET}{members}{order}} ? @{$Bgruven{BINFO}->{DSET}{members}{order}} : @{$Bgruven{BINFO}->{DSET}{model}}) {
        foreach my $stnm (sort { $a <=> $b } keys %{$Bgruven{PROCESS}->{STATIONS}{valid}}) {
            my $locfil;
            for ($locfil = $Bgruven{BINFO}->{DSET}{locfil}) {s/STNM/$stnm/g; s/MOD|MEMBER/$mod/g; $_="$Bgruven{GRUVEN}->{DIRS}{bufdir}/$locfil";}
            &Utils::rm($locfil) if $Bgruven{GRUVEN}->{OPTS}{forced};
            $Bgruven{PROCESS}->{STATIONS}{acquire}{$mod}{$stnm} = $locfil unless -s $locfil;
            $Bgruven{PROCESS}->{STATIONS}{process}{$mod}{$stnm} = $locfil if -s $locfil and ($Bgruven{GRUVEN}->{OPTS}{forcep} or $n2p{$stnm});
        }
    }

return 1;
}


sub acquire {
#----------------------------------------------------------------------------------
#  Go and get get the BUFR files
#----------------------------------------------------------------------------------
#
use List::Util qw(shuffle);
use Method;
use Data::Dumper; $Data::Dumper::Sortkeys = 1;

#  If you are using Perl version 5.10 or higher then comment out the "use Switch 'Perl6'"
#  statement and uncomment the use feature "switch" line.
#
use Switch 'Perl6';      #  < Perl V5.10
#use feature "switch";   #  For Perl V5.10 and above

    my @missmbrs=();

    foreach my $meth (shuffle keys %{$Bgruven{PROCESS}->{SOURCES}}) {

        foreach my $host (shuffle keys %{$Bgruven{PROCESS}->{SOURCES}{$meth}}) {

            my $rfile = $Bgruven{PROCESS}->{SOURCES}{$meth}{$host};

            #  Create hash of BUFR files and location on remote host
            #
            my %bufrs=();

            foreach my $mod (sort keys %{$Bgruven{PROCESS}->{STATIONS}{acquire}}) {
                foreach my $stnm (sort {$a <=> $b} keys %{$Bgruven{PROCESS}->{STATIONS}{acquire}{$mod}}) {

                    if (-s $Bgruven{PROCESS}->{STATIONS}{acquire}{$mod}{$stnm}) {
                        push @{$Bgruven{DATA}->{BUFR}} => $Bgruven{PROCESS}->{STATIONS}{acquire}{$mod}{$stnm};
                        $Bgruven{PROCESS}->{STATIONS}{process}{$mod}{$stnm} = $Bgruven{PROCESS}->{STATIONS}{acquire}{$mod}{$stnm}; next;
                    }
                    (my $rf = $rfile) =~ s/STNM/$stnm/g; $rf =~ s/MOD|MEMBER/$mod/g;
                    $bufrs{$Bgruven{PROCESS}->{STATIONS}{acquire}{$mod}{$stnm}} = $rf;
                }
            }

            #  Attempt to aquire the BUFR files
            #
            if (%bufrs) {
                given ($meth) {
                    when (/http/i) {&Method::http($host,%bufrs);}
                    when (/nfs/i)  {&Method::copy($host,%bufrs);}
                    when (/ftp/i)  {&Method::ftp($host,%bufrs) ;}
                }
            }


            #  Test whether all files were acquired
            #
            my %missing=();
            @missmbrs  =();
            foreach my $mod (sort keys %{$Bgruven{PROCESS}->{STATIONS}{acquire}}) {
                foreach my $stnm (sort {$a <=> $b} keys %{$Bgruven{PROCESS}->{STATIONS}{acquire}{$mod}}) {
                    if (-s $Bgruven{PROCESS}->{STATIONS}{acquire}{$mod}{$stnm}) {
                        push @{$Bgruven{DATA}->{BUFR}} => $Bgruven{PROCESS}->{STATIONS}{acquire}{$mod}{$stnm};
                        $Bgruven{PROCESS}->{STATIONS}{process}{$mod}{$stnm} = $Bgruven{PROCESS}->{STATIONS}{acquire}{$mod}{$stnm}; next;
                    }
                    $missing{$mod}{$stnm} = $Bgruven{PROCESS}->{STATIONS}{acquire}{$mod}{$stnm};
                    push @missmbrs => $stnm;
                }
            }

            %{$Bgruven{PROCESS}->{STATIONS}{acquire}} = %missing;
            
            unless (%missing) {&Utils::modprint(0,9,96,1,2,"It's a great day! All requested BUFR files have been downloaded"); return 1;}
        }

    }

    if ($Bgruven{GRUVEN}->{OPTS}{debug}) {
        open DEBUGFL => ">$Bgruven{GRUVEN}->{DIRS}{debug}/acquire.debug.$$";
        my $dd = Dumper \%Bgruven; $dd =~ s/    / /mg;print DEBUGFL $dd;
        close DEBUGFL;
    }

    #  This code should eliminate the processing of SREF stations when there is one or more
    #  members missing.
    #
    #  Uncomment the following to change the behaviour such that SREF stations will not be 
    #  processed until all members exist locally on the system.
    #
#   foreach my $mod (sort keys %{$Bgruven{PROCESS}->{STATIONS}{process}}) {
#       foreach my $stn (&Utils::rmdups(@missmbrs)) {delete $Bgruven{PROCESS}->{STATIONS}{process}{$mod}{$stn} if exists $Bgruven{PROCESS}->{STATIONS}{process}{$mod}{$stn};}
#   }

    &Utils::modprint(0,9,96,1,2,"Missed few BUFR files this time. Next time I'll do a better job!");

return 1;
}