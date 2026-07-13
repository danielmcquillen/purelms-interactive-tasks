# FIXME: before you push into master...
RUNTIMEDIR=/usr/bin/../include/omc/c/
#COPY_RUNTIMEFILES=$(FMI_ME_OBJS:%= && (OMCFILE=% && cp $(RUNTIMEDIR)/$$OMCFILE.c $$OMCFILE.c))

fmu:
	rm -f 482.fmutmp/sources/HydronicLoop_init.xml
	cp -a "/usr/bin/../share/omc/runtime/c/fmi/buildproject/"* 482.fmutmp/sources
	cp -a HydronicLoop_FMU.libs 482.fmutmp/sources/
