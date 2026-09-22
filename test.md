# test

## Form Metadata

- **Caption:** Change Request Maintenance - [ap1 ap1: 2015/03/11]
- **Record source:** `MODI_REC`
- **Behavior:** OnUnload: Form_Unload; OnDirty: Form_Dirty; OnCurrent: Form_Current; OnOpen: Form_Open; OnActivate: Form_Activate; OnError: Form_Error; AllowAdditions=non-default, FilterOnLoad=0

## Section `Detail`

| Control | Caption | Binding / Events / Rules |
| --- | --- | --- |
| TextBox `AI_REF_NO` | Bank Ref. No. | ControlSource=AI_REF_NO; Locked=non-default |
| CommandButton `bSave` | &Save | OnClick: bSave_Click |
| CommandButton `bApprove` | A&pprove | OnClick: bApprove_Click |
| CommandButton `bOnHold` | &OnHold | OnClick: bOnHold_Click |
| CommandButton `bReject` | Re&ject | OnClick: bReject_Click; Enabled=non-default |
| CommandButton `bSubmitForApproval` | Submit for &Approval | OnClick: bSubmitForApproval_Click; Visible=non-default, Enabled=non-default |
| CommandButton `bLoanSchedule` | Pre&view Loan Schedule | OnClick: bLoanSchedule_Click |
| ComboBox `MODI_STATUS_COPY` | Status | ControlSource=MODI_STATUS, RowSourceType=Table/Query, RowSource=Q_CMKH_MODI_STATUS_LOV; Locked=non-default |
| TextBox `MODI_SEQ_NO` | - | ControlSource=MODI_SEQ_NO; Locked=non-default |
| Label `Label427` | Change Request Sequence No. | - |
| ComboBox `PL_NO` | PL No. | ControlSource=PL_NO, RowSourceType=Table/Query, RowSource=SELECT * FROM Q_PL_NO_LOV; Locked=non-default |

### Tab `tabMain`

#### Request Details

| Control | Caption | Binding / Events / Rules |
| --- | --- | --- |
| ComboBox `aiNo` | AI No. | RowSourceType=Table/Query, RowSource=SELECT * FROM Q_AI_NO_LOV; Locked=non-default |
| TextBox `AI_LOAN_NO` | AI Loan No. | ControlSource=AI_LOAN_NO; Locked=non-default |
| TextBox `AI_CONTACT_PERSON` | Name of Contact Person | ControlSource=AI_CONTACT_PERSON; Locked=non-default |
| TextBox `AI_CONTACT_TEL_NO` | Contact Person's Phone No. | ControlSource=AI_CONTACT_TEL_NO; Locked=non-default |
| TextBox `AI_CONTACT_FAX_NO` | Contact Person's Fax No. | ControlSource=AI_CONTACT_FAX_NO; Locked=non-default |
| TextBox `AI_MODI_INPUT_DATE` | Loan Modification Created Date | ControlSource=AI_MODI_INPUT_DATE |
| TextBox `AI_MODI_SUBMIT_DATE` | Application Submission Date | ControlSource=AI_MODI_SUBMIT_DATE; Locked=non-default |
| TextBox `LUMP_SUM_PREM_PAYM_FROM` | Premium Payment Amount  (From) | ControlSource=LUMP_SUM_PREM_PAYM_FROM; Locked=non-default |
| TextBox `LUMP_SUM_PREM_PAYM_TO` | Premium Payment Amount (To) | ControlSource=LUMP_SUM_PREM_PAYM_TO |
| TextBox `LUMP_SUM_LOAN_REFIN_FROM` | Repayment of Existing Loan (From) | ControlSource=LUMP_SUM_LOAN_REFIN_FROM; Locked=non-default |
| TextBox `LUMP_SUM_LOAN_REFIN_TO` | Repayment of Existing Loan (To) | ControlSource=LUMP_SUM_LOAN_REFIN_TO |
| TextBox `LUMP_SUM_PROP_REPAIR_FROM` | Home Improvement, Repairs or Maintenance (From) | ControlSource=LUMP_SUM_PROP_REPAIR_FROM; Locked=non-default |
| TextBox `LUMP_SUM_PROP_REPAIR_TO` | Home Improvement, Repairs or Maintenance (To) | ControlSource=LUMP_SUM_PROP_REPAIR_TO |
| TextBox `LUMP_SUM_EPA_FEE_FROM` | Lump Sum Payout for EPA / Court Order Fee (From) | ControlSource=LUMP_SUM_EPA_FEE_FROM; Locked=non-default |
| TextBox `LUMP_SUM_EPA_FEE_TO` | Lump Sum Payout for EPA / Court Order Fee (To) | ControlSource=LUMP_SUM_EPA_FEE_TO |
| TextBox `LUMP_SUM_SET_ASIDE_PR_FROM` | Lump Sum Payout to be Set Aside (Property Repairs) (From) | ControlSource=LUMP_SUM_SET_ASIDE_PR_FROM; Locked=non-default |
| TextBox `LUMP_SUM_SET_ASIDE_PR_TO` | Lump Sum Payout Set Aside (Property Repairs) (To) | ControlSource=LUMP_SUM_SET_ASIDE_PR_TO |
| CheckBox `lsSetAsidePrUnusedInd` | - | OnClick: lsSetAsidePrUnusedInd_Click; Locked=non-default |
| TextBox `LUMP_SUM_SET_ASIDE_OTHERS_FROM` | Lump Sum Payout to be Set Aside (Other Purposes) (From) | ControlSource=LUMP_SUM_SET_ASIDE_OTHERS_FROM; Locked=non-default |
| TextBox `LUMP_SUM_SET_ASIDE_OTHERS_TO` | Lump Sum Payout Set Aside (Other Purposes) (To) | ControlSource=LUMP_SUM_SET_ASIDE_OTHERS_TO |
| CheckBox `lsSetAsideOthersUnusedInd` | - | OnClick: lsSetAsideOthersUnusedInd_Click; Locked=non-default |
| TextBox `LUMP_SUM_OTHER_APPLICANT_FROM` | Any Other Purposes (to Applicant(s)) (From) | ControlSource=LUMP_SUM_OTHER_APPLICANT_FROM; Locked=non-default |
| TextBox `LUMP_SUM_OTHER_APPLICANT_TO` | Any Other Purposes (to Applicant(s)) (To) | ControlSource=LUMP_SUM_OTHER_APPLICANT_TO |
| TextBox `LUMP_SUM_OTHER_OTHERS_FROM` | Any Other Purposes (to Others) (From) | ControlSource=LUMP_SUM_OTHER_OTHERS_FROM; Locked=non-default |
| TextBox `LUMP_SUM_OTHER_OTHERS_TO` | Any Other Purposes (to Others) (To) | ControlSource=LUMP_SUM_OTHER_OTHERS_TO |
| TextBox `TOTAL_AMOUNT_TO_APPLICANT_FROM` | Total Amount to Applicant(s) (From) | Locked=non-default |
| TextBox `TOTAL_AMOUNT_TO_APPLICANT_TO` | Total Amount to Applicant(s) (To) | Locked=non-default |
| TextBox `LUMP_SUM_AMT_FROM` | Amount of Lump Sum Payout (From) | ControlSource=LUMP_SUM_AMT_FROM; Locked=non-default |
| TextBox `LUMP_SUM_AMT_TO` | Amount of Lump Sum Payout | ControlSource=LUMP_SUM_AMT_TO; Locked=non-default |
| TextBox `FINANCED_FEE_FROM` | Financed Fee (From) | ControlSource=FINANCED_FEE_FROM; Locked=non-default |
| TextBox `FINANCED_FEE_TO` | Financed Fee (To) | ControlSource=FINANCED_FEE_TO |
| TextBox `ESTATE_AGENT_FEE` | Financed Fee (From) | ControlSource=ESTATE_AGENT_FEE |
| TextBox `ADMIN_FEE` | Administration Fee(s) | ControlSource=ADMIN_FEE |
| TextBox `BUILD_INSPECT_FEE` | Building Inspection Fee(s) | ControlSource=BUILD_INSPECT_FEE |
| TextBox `LEGAL_FEE` | Legal Fee(s) | ControlSource=LEGAL_FEE |
| TextBox `T_CLOSING_DATE_FROM` | Tentative Closing Date (From) | ControlSource=T_CLOSING_DATE_FROM; Locked=non-default |
| TextBox `T_CLOSING_DATE_TO` | Tentative Closing Date (To) | ControlSource=T_CLOSING_DATE_TO |
| TextBox `SPECIFIED_PROP_VALUE_FROM` | Specified Property Value (From) | ControlSource=SPECIFIED_PROP_VALUE_FROM; Locked=non-default |
| TextBox `SPECIFIED_PROP_VALUE_TO` | Specified Property Value (To) | ControlSource=SPECIFIED_PROP_VALUE_TO |
| TextBox `PARTIAL_REPAYMENT` | Total Amount to Applicant(s) (From) | ControlSource=PARTIAL_REPAYMENT |
| TextBox `EFF_DATE` | Effective Date | ControlSource=EFF_DATE; Locked=non-default |
| ComboBox `CONFIRM_IND` | Confirm Indicator | ControlSource=CONFIRM_IND, RowSourceType=Table/Query, RowSource=Q_YES_NO_IND_LOV; Locked=non-default, DefaultValue=\\"N\\" |
| TextBox `HANDLING_FEE` | Handling Fee | ControlSource=HANDLING_FEE; Locked=non-default |
| TextBox `BANK_CHARGES` | Bank Charges | ControlSource=BANK_CHARGES; Locked=non-default |
| TextBox `AI_OTHERS` | - | ControlSource=AI_OTHERS; Locked=non-default |
| Label `Label404` | From | - |
| Label `Label405` | To | - |
| Label `Label424` | Others | - |
| Label `Label441` | Purposes for Lump Sum Payout(s) | - |
| Label `Label442` | 2. Repayment of Existing Loan | - |
| Label `Label443` | 1. Premium Payment Amount | - |
| Label `Label444` | 3. Home Improvement, Repairs or Maintenance | - |
| Label `Label445` | 7. Any Other Purposes (to Applicant(s)) | - |
| Label `Label446` | 8. Any Other Purposes (to Others) | - |
| Label `Label469` | Effective Date | - |
| Label `Label470` | Handling Fee | - |
| Label `Label471` | Bank Charges | - |
| Label `Label477` | Confirm Indicator | - |
| Label `Label480` | Unused | - |
| Label `Label478` | 4. Lump Sum Payout for EPA / Court Order Fee | - |
| Label `Label447` | Total Amount to Applicant(s) | - |
| Label `lbPartial Prepayment` | Partial Repayment | - |

##### OptionGroup `Frame286`

| Control | Caption | Binding / Events / Rules |
| --- | --- | --- |
| Label `Label287` | AI's Information | - |

##### OptionGroup `Frame289`

| Control | Caption | Binding / Events / Rules |
| --- | --- | --- |
| Label `Label290` | Change Request Details | - |

#### Status

| Control | Caption | Binding / Events / Rules |
| --- | --- | --- |
| CommandButton `bReassignHandler` | &Reassign Handler | OnClick: bReassignHandler_Click |
| ComboBox `HANDLER` | Handled By | ControlSource=HANDLER, RowSourceType=Table/Query, RowSource=SELECT * FROM Q_ALL_USER_LOV WHERE AI_NO = 'CMKH'; Locked=non-default |
| ComboBox `AI_MODI_STATUS` | AI's Status | ControlSource=AI_MODI_STATUS, RowSourceType=Table/Query, RowSource=Q_AI_MODI_STATUS_LOV; Locked=non-default |
| TextBox `STATUS_UPD_DATE` | Status Changed On | ControlSource=STATUS_UPD_DATE; Locked=non-default |
| ComboBox `STATUS_UPD_USER` | Status Changed By | ControlSource=STATUS_UPD_USER, RowSourceType=Table/Query, RowSource=SELECT * FROM Q_ALL_USER_LOV WHERE USER_NO = 'CMKH_ap1'; Locked=non-default |
| TextBox `MODI_REMARKS` | Counter-Offer | ControlSource=MODI_REMARKS |
| ComboBox `LAST_UPD_USER` | Last Updated User | ControlSource=LAST_UPD_USER, RowSourceType=Table/Query, RowSource=SELECT * FROM Q_ALL_USER_LOV WHERE USER_NO = 'CMKH_ap1'; Locked=non-default |
| TextBox `LAST_UPD_DATE` | Last Updated Date | ControlSource=LAST_UPD_DATE; Locked=non-default |
| ComboBox `MODI_STATUS` | CMKHI Status | ControlSource=MODI_STATUS, RowSourceType=Table/Query, RowSource=SELECT * FROM Q_CMKH_MODI_STATUS_LOV WHERE CODE = 'RJ'; Locked=non-default |
| TextBox `MODI_CONDITIONS` | Change Request Condition | ControlSource=MODI_CONDITIONS |
| CommandButton `bPrintReply` | Print Reply | OnClick: bPrintReply_Click |
| CommandButton `bPrintHistory` | Print &History | OnClick: bPrintHistory_Click |
| TextBox `Text479` | CMKHI Remarks | ControlSource=CMKH_REMARKS |

##### OptionGroup `Frame274`

| Control | Caption | Binding / Events / Rules |
| --- | --- | --- |
| Label `Label275` | Information from CMKHI | - |
