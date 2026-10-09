**free
/copy LIB1/QRPGLESRC,CONSTANTS
dcl-s balance packed(9:2) inz(0);
dcl-f CUSTOMER usage(*input:*output) keyed;
dcl-proc validate;
if (balance + increment) > 0;
  dow balance < 10;
    balance += increment;
  enddo;
else;
  balance = 0;
endif;
monitor;
  chain customerId CUSTOMER;
  update CUSTOMER;
on-error;
  balance = -1;
endmon;
callp notify(balance);
return;
end-proc;
